import time
from datetime import datetime
from database import db, Strategy
from oracle import get_live_prices
from fhe_client import is_condition_met
from trade_executor import execute_trade, execute_private_withdrawal
from config import CHECK_INTERVAL_SECONDS, PYTH_PRICE_FEED_IDS

def worker_loop(app):
    print("[Scheduler] Starting Solana worker loop")

    while True:
        try:
            with app.app_context():
                pending_strategies = Strategy.query.filter_by(status='PENDING').all()

                if not pending_strategies:
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                strategies_to_process = [s.to_dict() for s in pending_strategies]

                # Get SOL price feed ID (primary asset on Solana)
                sol_feed_id = PYTH_PRICE_FEED_IDS.get("SOL")
                if not sol_feed_id:
                    print("[Scheduler] Error: SOL Price Feed ID not found in config.")
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                # Also get ETH and BTC prices for multi-asset strategies
                feed_ids = [
                    sol_feed_id,
                    PYTH_PRICE_FEED_IDS.get("ETH"),
                    PYTH_PRICE_FEED_IDS.get("BTC"),
                ]
                feed_ids = [f for f in feed_ids if f]  # Filter out None

                live_prices = get_live_prices(feed_ids)

                if sol_feed_id not in live_prices:
                    print("[Scheduler] Warning: SOL price not available from oracle this cycle. Retrying.")
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                current_sol_price = live_prices[sol_feed_id]
                print(f"[Scheduler] Processing {len(strategies_to_process)} strategies. Current SOL price: ${current_sol_price:,.2f}")

                for strategy_dict in strategies_to_process:
                    try:
                        # Determine which price to use based on strategy asset
                        asset_in = strategy_dict.get('asset_in', 'SOL').upper()
                        price_feed_id = strategy_dict.get('price_feed_id') or PYTH_PRICE_FEED_IDS.get(asset_in)

                        if price_feed_id and price_feed_id in live_prices:
                            current_price = live_prices[price_feed_id]
                        else:
                            current_price = current_sol_price  # Default to SOL

                        if is_condition_met(strategy_dict, current_price):
                            print(f"[Scheduler] Condition met for Strategy ID {strategy_dict.get('id')}. Executing...")

                            # Check if this is a private withdrawal
                            is_private = strategy_dict.get('is_private', False)

                            if is_private:
                                tx_hash = execute_private_withdrawal(strategy_dict, current_price)
                            else:
                                tx_hash = execute_trade(strategy_dict, current_price)

                            if tx_hash:  # tx_hash is now returned instead of boolean
                                strategy_to_update = Strategy.query.get(strategy_dict['id'])
                                if strategy_to_update:
                                    strategy_to_update.status = 'EXECUTED'
                                    strategy_to_update.tx_hash = tx_hash
                                    strategy_to_update.executed_at = datetime.utcnow()
                                    db.session.commit()
                                    print(f"[Scheduler] Strategy {strategy_dict.get('id')} marked as EXECUTED with tx: {tx_hash}")
                            else:
                                print(f"[Scheduler] Strategy {strategy_dict.get('id')} execution failed, will retry.")

                    except Exception as strategy_err:
                        print(f"[Scheduler] Error processing individual strategy {strategy_dict.get('id')}: {strategy_err}")
                        continue

        except Exception as e:
            print(f"[Scheduler] Global loop error: {e}")
            import traceback
            traceback.print_exc()

        time.sleep(CHECK_INTERVAL_SECONDS)
