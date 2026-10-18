#!/usr/bin/env python3
"""Accepts a dual-funded open, and an RBF of it, with an input of our own
from a PSBT with locktime 0, which reaches dualopend without a fallback
locktime."""

from pyln.client import Plugin

plugin = Plugin()


@plugin.hook('openchannel2')
def on_openchannel2(openchannel2, plugin, **kwargs):
    psbt = plugin.rpc.call('fundpsbt', {
        'satoshi': 100000,
        'feerate': '{}perkw'.format(openchannel2['funding_feerate_per_kw']),
        'startweight': 0,
        'min_witness_weight': 110,
        'excess_as_change': True,
        'locktime': 0,
        'nonwrapped': True})['psbt']
    return {'result': 'continue', 'psbt': psbt,
            'our_funding_msat': 100000 * 1000}


@plugin.hook('rbf_channel')
def on_rbf_channel(rbf_channel, plugin, **kwargs):
    utxos = ['{}:{}'.format(o['txid'], o['output'])
             for o in plugin.rpc.listfunds()['outputs'] if o['reserved']]
    psbt = plugin.rpc.call('utxopsbt', {
        'satoshi': 100000,
        'feerate': '{}perkw'.format(rbf_channel['funding_feerate_per_kw']),
        'startweight': 0,
        'utxos': utxos,
        'reservedok': True,
        'reserve': 0,
        'min_witness_weight': 110,
        'excess_as_change': True,
        'locktime': 0})['psbt']
    return {'result': 'continue', 'psbt': psbt,
            'our_funding_msat': 100000 * 1000}


@plugin.hook('openchannel2_sign')
def on_openchannel2_sign(openchannel2_sign, plugin, **kwargs):
    return {'result': 'continue',
            'psbt': plugin.rpc.signpsbt(openchannel2_sign['psbt'])['signed_psbt']}


plugin.run()
