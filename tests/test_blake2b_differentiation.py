"""Mandatory peer feature without invoice modifications."""
from fixtures import *  # noqa: F401,F403
from pyln.client import RpcError
from utils import TEST_NETWORK, sync_blockheight, wait_for
import pytest

pytestmark = pytest.mark.skipif(TEST_NETWORK != 'regtest', reason='Blake2b peer features do not apply to Elements')


# A peer which has not upgraded has neither bit; option_unified_sigs depends
# on option_blake2b, so dropping only the latter leaves an incoherent vector.
NO_BLAKE2B = ['-514', '-512']


@pytest.mark.parametrize('incoming', [False, True])
@pytest.mark.parametrize('peer_features', [NO_BLAKE2B, '513/////////'])
def test_blake2b_required_peer_bit(node_factory, incoming, peer_features):
    blake, legacy = node_factory.get_nodes(2, opts=[
        {'may_reconnect': True, 'allow_warning': True},
        {'dev-force-features': peer_features, 'may_reconnect': True, 'allow_warning': True}])
    source, target = (legacy, blake) if incoming else (blake, legacy)
    # The two refuse each other, but by different routes: a peer that withholds
    # bit 512 hangs up on our compulsory even bit, while one that offers only
    # 513 is turned away by us.  Only the second reliably fails the caller's
    # connect, since the first can land after the RPC has already replied.  So
    # assert the settled state, which holds either way, rather than racing the
    # hangup.
    try:
        source.rpc.connect(target.info['id'], 'localhost', target.port)
    except RpcError:
        pass
    wait_for(lambda: not any(p['connected'] for p in blake.rpc.listpeers()['peers']))

    if peer_features != NO_BLAKE2B:
        return

    # The payment artifacts separate too.  Bit 512 is even, so a reader which
    # does not know it refuses ours outright; we read theirs and say what it
    # is, but we will not pay it.
    ours = blake.rpc.invoice(1000, 'ours', 'ours')['bolt11']
    with pytest.raises(RpcError, match=r'unknown feature bit 512'):
        legacy.rpc.decode(ours)

    theirs = legacy.rpc.invoice(1000, 'theirs', 'theirs')['bolt11']
    assert blake.rpc.decode(theirs)['valid']
    with pytest.raises(RpcError, match=r'does not set option_blake2b'):
        blake.rpc.xpay(theirs)


def test_blake2b_peers_connect(node_factory):
    a, b = node_factory.get_nodes(2)
    a.rpc.connect(b.info['id'], 'localhost', b.port)
    assert a.rpc.listpeers()['peers'][0]['connected']


_INIT_BITS = [0, 5, 7, 8, 11, 12, 14, 17, 19, 23, 25, 27, 35, 39, 43, 44, 47,
              51, 63, 512, 515]
# node_announcement also carries keysend.
_NODE_BITS = sorted(_INIT_BITS + [55])


def _forced_features(**places):
    """Build a --dev-force-features value naming every feature place."""
    order = ['init', 'globalinit', 'node_announce', 'channel', 'bolt11',
             'b12offer', 'b12invreq', 'b12inv', 'channel_type']
    return '/'.join(','.join(str(b) for b in places.get(p, []))
                    for p in order) + '/'


# Sets option_blake2b everywhere it needs it to connect and to read our offer,
# but leaves it out of the invoice_request it builds.
INVREQ_WITHOUT_BLAKE2B = _forced_features(init=_INIT_BITS,
                                          node_announce=_NODE_BITS,
                                          b12offer=[512],
                                          b12invreq=[],
                                          b12inv=[512])


def test_blake2b_invreq_without_bit_refused(node_factory):
    """We do not hand an invoice to a request which does not follow the rules.

    The request arrives in an onion message, which never goes through
    invrequest_decode(), so the reader has to check for itself.
    """
    l1, l2 = node_factory.get_nodes(2, opts=[
        {},
        {'dev-force-features': INVREQ_WITHOUT_BLAKE2B}])
    l2.rpc.connect(l1.info['id'], 'localhost', l1.port)

    offer = l1.rpc.offer('any', 'refuse me')['bolt12']
    # l2 reads the offer happily: it knows bit 512 in that place.
    assert l2.rpc.decode(offer)['valid']

    with pytest.raises(RpcError, match=r'option_blake2b'):
        l2.rpc.fetchinvoice(offer, 1000)


NOT_BLAKE2B = 'is at or above the BLAKE2b activation height 110 but has an 80-byte header'


@pytest.mark.parametrize('bitcoind', [False], indirect=True)
def test_backend_without_blake2b_at_startup(node_factory, bitcoind):
    """A backend that never switched to BLAKE2b must stop us before we sync."""
    bitcoind.env['BLAKE2B_ACTIVATION_HEIGHT'] = '100000'
    bitcoind.start()
    bitcoind.generate_block(120)
    l1 = node_factory.get_node(start=False, may_fail=True, broken_log='80-byte header',
                               options={'dev-blake2b-activation-height': 110})
    l1.daemon.start(wait_for_initialized=False, stderr_redir=True)
    assert l1.daemon.wait() != 0
    assert l1.daemon.is_in_stderr(NOT_BLAKE2B)


@pytest.mark.parametrize('bitcoind', [False], indirect=True)
def test_backend_without_blake2b_at_activation(node_factory, bitcoind):
    """A backend that stays on 80-byte headers past the activation stops us there."""
    bitcoind.env['BLAKE2B_ACTIVATION_HEIGHT'] = '100000'
    bitcoind.start()
    bitcoind.generate_block(101)
    # Past startup, fatal() aborts, so the crash report is expected too.
    l1 = node_factory.get_node(start=False, may_fail=True,
                               broken_log='80-byte header|FATAL SIGNAL|backtrace',
                               options={'dev-blake2b-activation-height': 110})
    l1.daemon.start(stderr_redir=True)
    l1.daemon.wait_for_log('Adding block 101')
    bitcoind.generate_block(10)
    assert l1.daemon.wait() != 0
    assert l1.daemon.is_in_stderr('Block 110 ' + NOT_BLAKE2B)
    assert l1.daemon.is_in_log('Adding block 109')
    assert not l1.daemon.is_in_log('Adding block 110')


def test_backend_with_blake2b_passes_activation(node_factory, bitcoind):
    """A BLAKE2b backend crosses the activation without complaint."""
    l1 = node_factory.get_node(options={'dev-blake2b-activation-height': 110})
    bitcoind.generate_block(20)
    sync_blockheight(bitcoind, [l1])
    assert l1.rpc.getinfo()['blockheight'] >= 120
