import time
from datetime import datetime
from database import db, Strategy, StrategyLeg, get_server_key
from oracle import get_live_prices
from fhe_client import get_encrypted_result, get_encrypted_leg_result
from condition_evaluator import evaluate_tree_encrypted
from config import CHECK_INTERVAL_SECONDS, PYTH_PRICE_FEED_IDS, DECRYPTOR_URL
from decryptor_client import decrypt_trigger, decryptor_enabled
from executor_runner import run_execution, run_leg_execution


def _price_for(strategy_dict, live_prices, eth_feed_id, eth_price, sol_price):
    asset_in = strategy_dict.get('asset_in', 'ETH').upper()
    price_feed_id = strategy_dict.get('price_feed_id') or PYTH_PRICE_FEED_IDS.get(asset_in)
    if price_feed_id and price_feed_id in live_prices:
        return live_prices[price_feed_id]
    if eth_feed_id and eth_feed_id in live_prices:
        return eth_price
    return sol_price


def _process_multi_leg(strategy_dict, current_price, server_key, user_id):
    """Evaluate + (in TEE mode) fire each eligible leg of a TWAP/Grid strategy independently.

    Each leg is a fully shielded sub-trade: it has its own encrypted trigger (price band for a
    grid rung, encrypted fire-time for a TWAP slice), its own slice-note ZK proof, and its own
    nullifier. A leg fires only when its OWN encrypted condition decrypts to TRUE inside the
    TEE, so a TWAP of N slices yields N independent withdraw+swap txs with N distinct nullifiers.
    The parent strategy becomes EXECUTED only once every leg has completed.
    """
    sid = strategy_dict.get('id')
    legs = StrategyLeg.query.filter(
        StrategyLeg.strategy_id == sid,
        StrategyLeg.status.in_(['PENDING', 'ARMED']),
    ).order_by(StrategyLeg.leg_index).all()

    now_ts = int(time.time())
    for leg in legs:
        leg_dict = leg.to_dict()
        enc_result = get_encrypted_leg_result(leg_dict, current_price, server_key, now_ts=now_ts)
        if not enc_result:
            continue

        # Store the per-leg encrypted result (browser arming path can decrypt this too).
        leg.encrypted_result = enc_result
        leg.result_updated_at = datetime.utcnow()
        if leg.status == 'PENDING':
            leg.status = 'ARMED'
        db.session.commit()

        if not (decryptor_enabled() and enc_result):
            continue  # arming-only mode: leave it for the browser to authorize

        triggered, dec_err = decrypt_trigger(user_id, enc_result)
        if dec_err:
            print(f"[Scheduler] Decryptor error for {sid} leg {leg.leg_index}: {dec_err}")
            continue
        if not triggered:
            continue

        # Re-fetch the leg — its status may have changed during decryption.
        leg = StrategyLeg.query.get(leg.id)
        if not leg or leg.status in ('EXECUTED', 'FAILED', 'EXECUTING'):
            continue
        print(f"[Scheduler] TEE trigger=TRUE for {sid} leg {leg.leg_index} — executing on-chain…")
        try:
            result = run_leg_execution(leg.to_dict(), strategy_dict, current_price)
            print(f"[Scheduler] {sid} leg {leg.leg_index} executed | {result}")
        except Exception as exec_err:
            print(f"[Scheduler] Execution failed for {sid} leg {leg.leg_index}: {exec_err}")

    # Mark the parent EXECUTED once all legs are done; FAILED if any leg failed and none remain.
    strat = Strategy.query.get(sid)
    if strat:
        remaining = StrategyLeg.query.filter(
            StrategyLeg.strategy_id == sid,
            StrategyLeg.status.in_(['PENDING', 'ARMED', 'EXECUTING']),
        ).count()
        done = StrategyLeg.query.filter_by(strategy_id=sid, status='EXECUTED').count()
        strat.executed_count = done
        if remaining == 0 and strat.status not in ('EXECUTED', 'FAILED'):
            failed = StrategyLeg.query.filter_by(strategy_id=sid, status='FAILED').count()
            strat.status = 'EXECUTED' if done > 0 else 'FAILED'
            if failed:
                print(f"[Scheduler] {sid} complete: {done} legs executed, {failed} failed")
        db.session.commit()


def worker_loop(app):
    mode = "TEE auto-execute" if decryptor_enabled() else "arming — browser decrypts & authorizes"
    print(f"[Scheduler] Starting worker loop ({mode})")

    while True:
        try:
            with app.app_context():
                # Process strategies awaiting a trigger. ARMED ones are refreshed each cycle
                # so the browser always sees a result reflecting the current price.
                pending = Strategy.query.filter(Strategy.status.in_(['PENDING', 'ARMED'])).all()

                if not pending:
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                strategies_to_process = [s.to_dict() for s in pending]

                feed_ids = [fid for fid in PYTH_PRICE_FEED_IDS.values() if fid]
                live_prices = get_live_prices(feed_ids)
                if not live_prices:
                    print("[Scheduler] Warning: No prices from oracle this cycle. Retrying.")
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                eth_feed_id = PYTH_PRICE_FEED_IDS.get("ETH")
                sol_feed_id = PYTH_PRICE_FEED_IDS.get("SOL")
                eth_price = live_prices.get(eth_feed_id, 0)
                sol_price = live_prices.get(sol_feed_id, 0)
                print(f"[Scheduler] Arming {len(strategies_to_process)} strategies. "
                      f"ETH=${eth_price:,.2f} SOL=${sol_price:,.2f}")

                # Cache server keys per user to avoid repeated (large) lookups within a cycle.
                server_keys = {}

                for strategy_dict in strategies_to_process:
                    sid = strategy_dict.get('id')
                    try:
                        user_id = strategy_dict.get('user_id')
                        server_key = server_keys.get(user_id)
                        if server_key is None:
                            server_key = get_server_key(user_id)
                            server_keys[user_id] = server_key
                        if not server_key:
                            print(f"[Scheduler] ⚠️ No server key for user {user_id}; skipping {sid}")
                            continue

                        current_price = _price_for(strategy_dict, live_prices, eth_feed_id, eth_price, sol_price)

                        # Multi-leg (TWAP / RANGE_GRID): evaluate + fire each leg independently.
                        if strategy_dict.get('leg_count'):
                            _process_multi_leg(strategy_dict, current_price, server_key, user_id)
                            continue

                        t_fhe = time.monotonic()
                        condition_tree = strategy_dict.get('condition_tree')
                        if condition_tree:
                            enc_result = evaluate_tree_encrypted(condition_tree, live_prices, server_key)
                        else:
                            enc_result = get_encrypted_result(strategy_dict, current_price, server_key)
                        fhe_ms = (time.monotonic() - t_fhe) * 1000

                        if not enc_result:
                            print(f"[Scheduler] ⚠️ No encrypted result for {sid} this cycle")
                            continue

                        strat = Strategy.query.get(sid)
                        if strat:
                            strat.encrypted_result = enc_result
                            strat.result_updated_at = datetime.utcnow()
                            if strat.status == 'PENDING':
                                strat.status = 'ARMED'
                            db.session.commit()
                        print(f"[Scheduler] Strategy {sid} armed | price={current_price:.2f} | fhe={fhe_ms:.0f}ms")

                        # Confidential VM path: decrypt result in TEE and execute server-side.
                        if decryptor_enabled() and enc_result:
                            triggered, dec_err = decrypt_trigger(user_id, enc_result)
                            if dec_err:
                                print(f"[Scheduler] Decryptor error for {sid}: {dec_err}")
                                continue
                            if not triggered:
                                continue
                            # Re-fetch row — status may have changed while we were decrypting.
                            strat = Strategy.query.get(sid)
                            if not strat or strat.status in ('EXECUTED', 'FAILED'):
                                continue
                            print(f"[Scheduler] TEE trigger=TRUE for {sid} — executing on-chain…")
                            try:
                                result = run_execution(strat.to_dict(), current_price)
                                print(f"[Scheduler] Strategy {sid} executed via TEE | {result}")
                            except Exception as exec_err:
                                print(f"[Scheduler] Execution failed for {sid}: {exec_err}")

                    except Exception as strategy_err:
                        print(f"[Scheduler] Error arming strategy {sid}: {strategy_err}")
                        continue

        except Exception as e:
            print(f"[Scheduler] Global loop error: {e}")
            import traceback
            traceback.print_exc()

        time.sleep(CHECK_INTERVAL_SECONDS)
