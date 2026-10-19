/* hsmd_sign_htlc_tx_mingle_unified signs our half of an HTLC tx
 * SIGHASH_ALL|SIGHASH_UNIFIED, over the final transaction, after the wallet
 * inputs; and refuses a request it cannot sign that way.
 *
 * We compile hsmd.c in, as run-bad-request-close does, and call libhsmd's
 * handler directly from a non-lightningd client, so a refusal returns NULL
 * rather than ending the process. */
#include "config.h"
#include <assert.h>
#include <bitcoin/chainparams.h>
#include <bitcoin/psbt.h>
#include <bitcoin/script.h>
#include <common/key_derive.h>
#include <common/setup.h>
#include <stdio.h>
#include <sys/socket.h>
#include <wally_psbt.h>

int unused_main(int argc, char *argv[]);
#define main unused_main
#include "../hsmd.c"
#undef main

static struct client *client;
static struct node_id peer_id;
static const u64 dbid = 1;
static const u64 commit_num = 3;

static const u8 *request(const u8 *msg)
{
	return tal_steal(tmpctx,
			 hsmd_handle_client_message(tmpctx, client->hsmd_client,
						    msg));
}

/* An HTLC input and output at index 0, then a fee input and change. */
static struct wally_psbt *htlc_psbt(const struct bitcoin_outpoint *htlc,
				    const u8 *wscript,
				    const struct hsm_utxo *coin,
				    size_t num_outputs)
{
	struct bitcoin_tx *tx = bitcoin_tx(tmpctx, chainparams, 2, 2, 0);
	/* The coin's prevout as lightningd might first describe it: hsmd
	 * replaces it for an anchor close output, and our signature must
	 * commit to the replacement. */
	struct pubkey stale;

	assert(pubkey_from_hexstr("02c6047f9441ed7d6d3045406e95c07cd85c778e4b8cef3ca7abac09b95c709ee5",
				  66, &stale));
	bitcoin_tx_add_input(tx, htlc, 1, NULL, AMOUNT_SAT(50000), NULL, wscript);
	bitcoin_tx_add_output(tx, scriptpubkey_p2wsh(tmpctx, wscript), NULL,
			      AMOUNT_SAT(50000));
	bitcoin_tx_add_input(tx, &coin->outpoint, 1, NULL, AMOUNT_SAT(1),
			     scriptpubkey_p2wpkh(tmpctx, &stale), NULL);
	if (num_outputs > 1)
		bitcoin_tx_add_output(tx, scriptpubkey_p2wpkh(tmpctx, &stale),
				      NULL, AMOUNT_SAT(15000));
	return tx->psbt;
}

static const u8 *sign(const struct hsm_utxo *coin, struct wally_psbt *psbt,
		      u32 htlc_input, const u8 *wscript)
{
	const struct hsm_utxo **utxos = tal_arr(tmpctx, const struct hsm_utxo *, 1);
	utxos[0] = coin;
	return request(towire_hsmd_sign_htlc_tx_mingle_unified(tmpctx, &peer_id,
							       dbid, utxos, psbt,
							       commit_num,
							       htlc_input,
							       wscript));
}

int main(int argc, char *argv[])
{
	int status_fds[2], client_fds[2];
	const struct chainparams *params;
	struct privkey priv;
	struct pubkey peer_key, per_commitment_point, htlc_pubkey, funding_pubkey;
	struct basepoints basepoints;
	struct secret *old_secret;
	struct unilateral_close_info close_info;
	struct hsm_utxo coin;
	struct bitcoin_outpoint htlc;
	struct bitcoin_signature sig;
	struct bitcoin_tx *tx;
	struct wally_psbt *psbt, *signed_psbt;
	const u8 *wscript, *reply;
	u8 hsmseed[32];
	u8 *msg;

	common_setup(argv[0]);
	uintmap_init(&clients);

	/* Refusals are reported to lightningd over status_conn. */
	assert(socketpair(AF_LOCAL, SOCK_STREAM, 0, status_fds) == 0);
	status_conn = daemon_conn_new(NULL, status_fds[0], NULL, NULL, NULL);
	status_setup_async(status_conn);

	params = chainparams_for_network("regtest");
	chainparams = params;
	memset(hsmseed, 1, sizeof(hsmseed));
	msg = hsmd_init(hsmseed, sizeof(hsmseed), 6,
			params->bip32_key_version, HSM_SECRET_PLAIN);
	assert(msg);
	taken(msg);
	tal_free(msg);

	memset(&priv, 1, sizeof(priv));
	assert(pubkey_from_privkey(&priv, &peer_key));
	node_id_from_pubkey(&peer_id, &peer_key);
	assert(socketpair(AF_LOCAL, SOCK_STREAM, 0, client_fds) == 0);
	client = new_client(NULL, params, &peer_id, dbid,
			    HSM_PERM_MASTER | HSM_PERM_COMMITMENT_POINT,
			    client_fds[0]);

	/* Our htlc key, the way lightningd derives it. */
	reply = request(towire_hsmd_get_channel_basepoints(tmpctx, &peer_id, dbid));
	assert(fromwire_hsmd_get_channel_basepoints_reply(reply, &basepoints,
							  &funding_pubkey));
	reply = request(towire_hsmd_get_per_commitment_point(tmpctx, commit_num));
	assert(fromwire_hsmd_get_per_commitment_point_reply(tmpctx, reply,
							    &per_commitment_point,
							    &old_secret));
	assert(derive_simple_key(&basepoints.htlc, &per_commitment_point,
				 &htlc_pubkey));

	/* Any witness script will do: hsmd only checks the prevout is it. */
	wscript = bitcoin_wscript_anchor(tmpctx, &htlc_pubkey);
	memset(&htlc, 0, sizeof(htlc));
	memset(&htlc.txid, 0x11, sizeof(htlc.txid));

	/* The fee input is our to_remote output from an anchor close. */
	close_info.channel_id = dbid;
	close_info.peer_id = peer_id;
	close_info.option_anchors = true;
	close_info.commitment_point = NULL;
	close_info.csv = 1;
	memset(&coin, 0, sizeof(coin));
	memset(&coin.outpoint.txid, 0x22, sizeof(coin.outpoint.txid));
	coin.outpoint.n = 1;
	coin.amount = AMOUNT_SAT(20000);
	coin.close_info = &close_info;
	coin.scriptPubkey = scriptpubkey_p2wsh(tmpctx,
		bitcoin_wscript_to_remote_anchored(tmpctx, &basepoints.payment, 1));

	psbt = htlc_psbt(&htlc, wscript, &coin, 2);
	reply = sign(&coin, psbt, 0, wscript);
	assert(reply);
	assert(fromwire_hsmd_sign_htlc_tx_mingle_unified_reply(tmpctx, reply,
								&signed_psbt,
								&sig));
	assert(sig.sighash_type == (SIGHASH_ALL | SIGHASH_UNIFIED));
	assert(signed_psbt->inputs[0].sighash == (SIGHASH_ALL | SIGHASH_UNIFIED));
	/* The fee input is signed too, and its prevout was replaced. */
	assert(signed_psbt->inputs[1].signatures.num_items == 1);
	assert(signed_psbt->inputs[1].witness_utxo->satoshi == 20000);

	/* Valid for our htlc key over the transaction as it will be sent... */
	tx = bitcoin_tx_with_psbt(tmpctx, signed_psbt);
	assert(check_tx_sig(tx, 0, NULL, wscript, &htlc_pubkey, &sig));
	/* ...and over nothing else: not with the fee input described as it was
	 * before hsmd signed it, nor with any output changed. */
	assert(!check_tx_sig(bitcoin_tx_with_psbt(tmpctx, psbt), 0, NULL,
			     wscript, &htlc_pubkey, &sig));
	bitcoin_tx_output_set_amount(tx, 1, AMOUNT_SAT(14999));
	assert(!check_tx_sig(tx, 0, NULL, wscript, &htlc_pubkey, &sig));

	/* A wallet utxo which is the HTLC input itself. */
	{
		struct hsm_utxo self = coin;
		self.outpoint = htlc;
		assert(!sign(&self, htlc_psbt(&htlc, wscript, &coin, 2), 0, wscript));
	}
	/* No such input. */
	assert(!sign(&coin, htlc_psbt(&htlc, wscript, &coin, 2), 2, wscript));
	/* No output to pair the HTLC input with. */
	{
		struct bitcoin_tx *bad = bitcoin_tx(tmpctx, chainparams, 2, 1, 0);
		bitcoin_tx_add_input(bad, &coin.outpoint, 1, NULL, coin.amount,
				     coin.scriptPubkey, NULL);
		bitcoin_tx_add_input(bad, &htlc, 1, NULL, AMOUNT_SAT(50000),
				     NULL, wscript);
		bitcoin_tx_add_output(bad, scriptpubkey_p2wsh(tmpctx, wscript),
				      NULL, AMOUNT_SAT(50000));
		assert(!sign(&coin, bad->psbt, 1, wscript));
	}
	/* Not the script the HTLC input spends. */
	assert(!sign(&coin, htlc_psbt(&htlc, wscript, &coin, 2), 0,
		     bitcoin_wscript_anchor(tmpctx, &funding_pubkey)));
	/* Asked to sign the HTLC input as anything but SIGHASH_ALL. */
	psbt = htlc_psbt(&htlc, wscript, &coin, 2);
	assert(wally_psbt_input_set_sighash(&psbt->inputs[0],
					    SIGHASH_SINGLE | SIGHASH_ANYONECANPAY
					    | SIGHASH_UNIFIED) == WALLY_OK);
	assert(!sign(&coin, psbt, 0, wscript));
	/* While a request it can sign still succeeds. */
	assert(sign(&coin, htlc_psbt(&htlc, wscript, &coin, 2), 0, wscript));

	close(client_fds[1]);
	close(status_fds[1]);
	tal_free(client->conn);
	tal_free(status_conn);
	hsmd_deinit();
	common_shutdown();
	return 0;
}
