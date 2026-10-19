#!/usr/bin/env python3
"""Puts an input from bitcoind's wallet into a dual-funded open we accept,
and has bitcoind sign it."""

from pyln.client import Plugin
import base64
import json
import urllib.request

plugin = Plugin()


def bitcoind(method, *params):
    req = urllib.request.Request(plugin.url,
                                 data=json.dumps({'jsonrpc': '1.0',
                                                  'id': 'df_external_input',
                                                  'method': method,
                                                  'params': list(params)}).encode())
    req.add_header('Authorization',
                   'Basic ' + base64.b64encode(plugin.auth.encode()).decode())
    with urllib.request.urlopen(req) as r:
        res = json.load(r)
    if res.get('error'):
        raise ValueError(res['error'])
    return res['result']


@plugin.method('dfext_setup')
def setup(plugin, utxo, change, amount, url, auth):
    """The input, change and amount we put in, and bitcoind's wallet RPC."""
    plugin.utxo = utxo
    plugin.change = change
    plugin.amount = amount
    plugin.url = url
    plugin.auth = auth
    return {}


@plugin.hook('openchannel2')
def on_openchannel2(openchannel2, plugin, **kwargs):
    psbt = bitcoind('createpsbt', [plugin.utxo], [plugin.change],
                    openchannel2['locktime'])
    psbt = bitcoind('walletprocesspsbt', psbt, False)['psbt']
    return {'result': 'continue',
            'psbt': plugin.rpc.setpsbtversion(psbt, 2)['psbt'],
            'our_funding_msat': plugin.amount * 1000}


@plugin.hook('openchannel2_sign')
def on_openchannel2_sign(openchannel2_sign, plugin, **kwargs):
    psbt = plugin.rpc.setpsbtversion(openchannel2_sign['psbt'], 0)['psbt']
    psbt = bitcoind('walletprocesspsbt', psbt)['psbt']
    psbt = bitcoind('finalizepsbt', psbt, False)['psbt']
    return {'result': 'continue',
            'psbt': plugin.rpc.setpsbtversion(psbt, 2)['psbt']}


plugin.run()
