#!/usr/bin/env python3
"""Puts an input into a dual-funded open we accept, then signs it with a
corrupted signature."""

from pyln.client import Plugin
import base64

plugin = Plugin()


def compact_size(b, i):
    n = b[i]
    if n < 0xfd:
        return n, i + 1
    size = {0xfd: 2, 0xfe: 4, 0xff: 8}[n]
    return int.from_bytes(b[i + 1:i + 1 + size], 'little'), i + 1 + size


def corrupt_first_partial_sig(psbt):
    """Flip a bit in the r value of the first input's partial signature."""
    b = bytearray(base64.b64decode(psbt))
    i = 5
    in_global = True
    while i < len(b):
        keylen, i = compact_size(b, i)
        if keylen == 0:
            in_global = False
            continue
        keytype = b[i]
        i += keylen
        vallen, i = compact_size(b, i)
        # PSBT_IN_PARTIAL_SIG: input maps come before output maps.
        if not in_global and keytype == 0x02:
            b[i + 7] ^= 1
            return base64.b64encode(bytes(b)).decode()
        i += vallen
    raise ValueError("no partial signature")


@plugin.hook('openchannel2')
def on_openchannel2(openchannel2, plugin, **kwargs):
    psbt = plugin.rpc.call('fundpsbt', {
        'satoshi': plugin.amount,
        'feerate': '{}perkw'.format(openchannel2['funding_feerate_per_kw']),
        'startweight': 0,
        'min_witness_weight': 110,
        'excess_as_change': True,
        'locktime': openchannel2['locktime'],
        'nonwrapped': True})['psbt']
    return {'result': 'continue', 'psbt': psbt,
            'our_funding_msat': plugin.amount * 1000}


@plugin.hook('openchannel2_sign')
def on_openchannel2_sign(openchannel2_sign, plugin, **kwargs):
    signed = plugin.rpc.signpsbt(openchannel2_sign['psbt'])['signed_psbt']
    return {'result': 'continue', 'psbt': corrupt_first_partial_sig(signed)}


@plugin.init()
def init(options, configuration, plugin):
    plugin.amount = int(options['bad-witness-amount'])


plugin.add_option('bad-witness-amount', '1000000', 'Satoshis we put in')
plugin.run()
