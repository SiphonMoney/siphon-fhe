import time
from datetime import datetime
from database import db, Strategy
from oracle import get_live_prices
from fhe_client import is_condition_met
from trade_executor import execute_trade, execute_private_withdrawal
from config import CHECK_INTERVAL_SECONDS, PYTH_PRICE_FEED_IDS


def worker_loop(app):
    print("[Scheduler] Starting worker loop")

    while True:
        try:
            with app.app_context():
                pending_strategies = Strategy.query.filter_by(status='PENDING').all()

                if not pending_strategies:
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                strategies_to_process = [s.to_dict() for s in pending_strategies]

                # Fetch prices for all supported assets
                feed_ids = [fid for fid in PYTH_PRICE_FEED_IDS.values() if fid]
                live_prices = get_live_prices(feed_ids)

                eth_feed_id = PYTH_PRICE_FEED_IDS.get("ETH")
                sol_feed_id = PYTH_PRICE_FEED_IDS.get("SOL")

                if not live_prices:
                    print("[Scheduler] Warning: No prices from oracle this cycle. Retrying.")
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                eth_price = live_prices.get(eth_feed_id, 0)
                sol_price = live_prices.get(sol_feed_id, 0)
                print(f"[Scheduler] Processing {len(strategies_to_process)} strategies. "
                      f"ETH=${eth_price:,.2f} SOL=${sol_price:,.2f}")

                for strategy_dict in strategies_to_process:
                    try:
                        asset_in = strategy_dict.get('asset_in', 'ETH').upper()
                        price_feed_id = strategy_dict.get('price_feed_id') or PYTH_PRICE_FEED_IDS.get(asset_in)

                        if price_feed_id and price_feed_id in live_prices:
                            current_price = live_prices[price_feed_id]
                        elif eth_feed_id and eth_feed_id in live_prices:
                            current_price = eth_price
                        else:
                            current_price = sol_price

                        sid = strategy_dict.get('id')

                        # ── 1. FHE evaluation ──────────────────────────────────
                        t_fhe = time.monotonic()
                        triggered = is_condition_met(strategy_dict, current_price)
                        fhe_ms = (time.monotonic() - t_fhe) * 1000

                        print(f"[Benchmark] strategy={sid}")
                        print(f"[Benchmark]   fhe_evaluation      = {fhe_ms:>8.1f} ms")
                        print(f"[Scheduler] Strategy {sid} | "
                              f"price={current_price:.2f} | fhe_check={fhe_ms:.0f}ms | triggered={triggered}")

                        if triggered:
                            is_private = strategy_dict.get('is_private', False)

                            # ── 2. Total execution ─────────────────────────────
                            t_exec = time.monotonic()

                            if is_private:
                                tx_hash = execute_private_withdrawal(strategy_dict, current_price)
                            else:
                                tx_hash = execute_trade(strategy_dict, current_price)

                            exec_ms = (time.monotonic() - t_exec) * 1000
                            print(f"[Benchmark]   total_execution     = {exec_ms:>8.1f} ms  tx={tx_hash}")
                            print(f"[Scheduler] Execution took {exec_ms:.0f}ms | tx_hash={tx_hash}")

                            if tx_hash:
                                strategy_to_update = Strategy.query.get(strategy_dict['id'])
                                if strategy_to_update:
                                    strategy_to_update.status = 'EXECUTED'
                                    strategy_to_update.tx_hash = tx_hash
                                    strategy_to_update.executed_at = datetime.utcnow()
                                    db.session.commit()
                                    print(f"[Scheduler] Strategy {strategy_dict.get('id')} EXECUTED: {tx_hash}")
                            else:
                                print(f"[Scheduler] Strategy {strategy_dict.get('id')} execution failed, will retry.")

                    except Exception as strategy_err:
                        print(f"[Scheduler] Error processing strategy {strategy_dict.get('id')}: {strategy_err}")
                        continue

        except Exception as e:
            print(f"[Scheduler] Global loop error: {e}")
            import traceback
            traceback.print_exc()

        time.sleep(CHECK_INTERVAL_SECONDS)
