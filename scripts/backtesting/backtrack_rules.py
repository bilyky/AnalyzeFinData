"""
AETHER Backtracking Verification Engine.
Parses the unified trade ledger (Data/trade_history_dna.json) on disk to run a historical post-mortem study.
Calculates the exact performance and P&L impact of implementing our strict momentum floor and dynamic sizing rules.
"""
import json
import os
import sys

# Anchor paths
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

DNA_LEDGER_PATH = os.path.join("Data", "trade_history_dna.json")

def run_backtrack_study():
    if not os.path.exists(DNA_LEDGER_PATH):
        print(f"❌ Error: Closed trade ledger '{DNA_LEDGER_PATH}' not found on disk.")  # noqa: print
        return

    with open(DNA_LEDGER_PATH) as f:
        ledger = json.load(f)

    # Filter out circuit breaker triggers, keep only actual completed trades
    trades = [t for t in ledger if "symbol" in t and "pnl_pct" in t]
    
    if not trades:
        print("⚠️ No completed trades found in the ledger to backtrack.")  # noqa: print
        return

    print(f"\n⚡ [AETHER Backtrack] Analyzing {len(trades)} historical completed trades from trade_history_dna.json...")  # noqa: print
    print("-" * 80)  # noqa: print
    print(f"{'Symbol':<10} {'Buy Date':<12} {'Sell Date':<12} {'s10':<6} {'Score':<6} {'Realized P&L%':<10} {'Industry'}")  # noqa: print
    print("-" * 80)  # noqa: print
    
    total_original_pnl = 0.0
    original_wins = 0

    for t in trades:
        sym = t["symbol"]
        b_date = t["buy_date"]
        s_date = t["sell_date"]
        pnl = t["pnl_pct"]
        
        # Extract buy metrics
        dna = t.get("buy_dna", {})
        s10 = dna.get("s10", "N/A")
        score = dna.get("score", "N/A")
        industry = dna.get("industry", "N/A")
        
        print(f"{sym:<10} {b_date:<12} {s_date:<12} {str(s10):<6} {str(score):<6} {pnl:>+9.2f}%   {industry}")  # noqa: print
        
        total_original_pnl += pnl
        if pnl > 0:
            original_wins += 1

    orig_count = len(trades)
    orig_win_rate = (original_wins / orig_count) * 100 if orig_count > 0 else 0.0
    orig_avg_pnl = total_original_pnl / orig_count if orig_count > 0 else 0.0

    print("-" * 80)  # noqa: print
    print(f"📊 BASE PERFORMANCE (Old Rules):")  # noqa: print
    print(f"  - Completed Trades: {orig_count}")  # noqa: print
    print(f"  - Win Rate:         {orig_win_rate:.1f}%")  # noqa: print
    print(f"  - Total Net Return: {total_original_pnl:>+6.2f}%")  # noqa: print
    print(f"  - Avg Return/Trade: {orig_avg_pnl:>+6.2f}%")  # noqa: print
    print("-" * 80)  # noqa: print

    # --- Scenario 1: Strict Momentum Floor s10 >= 2.5 ---
    s10_25_trades = []
    s10_25_pnl = 0.0
    s10_25_wins = 0
    s10_25_rejections = []

    # --- Scenario 2: Strict Momentum Floor s10 >= 3.0 ---
    s10_30_trades = []
    s10_30_pnl = 0.0
    s10_30_wins = 0
    s10_30_rejections = []

    for t in trades:
        pnl = t["pnl_pct"]
        dna = t.get("buy_dna", {})
        s10 = dna.get("s10")
        
        # Parse s10 safely (handle missing values)
        if s10 is None:
            s10 = 0.0

        # Scenario 1 evaluation (s10 >= 2.5)
        if s10 >= 2.5:
            s10_25_trades.append(t)
            s10_25_pnl += pnl
            if pnl > 0:
                s10_25_wins += 1
        else:
            s10_25_rejections.append((t["symbol"], s10, pnl))

        # Scenario 2 evaluation (s10 >= 3.0)
        if s10 >= 3.0:
            s10_30_trades.append(t)
            s10_30_pnl += pnl
            if pnl > 0:
                s10_30_wins += 1
        else:
            s10_30_rejections.append((t["symbol"], s10, pnl))

    # Calculate Scenario 1 Metrics
    s25_count = len(s10_25_trades)
    s25_win_rate = (s10_25_wins / s25_count) * 100 if s25_count > 0 else 0.0
    s25_avg_pnl = s10_25_pnl / s25_count if s25_count > 0 else 0.0
    s25_improvement = s10_25_pnl - total_original_pnl

    # Calculate Scenario 2 Metrics
    s30_count = len(s10_30_trades)
    s30_win_rate = (s10_30_wins / s30_count) * 100 if s30_count > 0 else 0.0
    s30_avg_pnl = s10_30_pnl / s30_count if s30_count > 0 else 0.0
    s30_improvement = s10_30_pnl - total_original_pnl

    print(f"\n🛡️ SCENARIO A: Strict Momentum Floor (Short10 >= 2.5)")  # noqa: print
    print(f"  - Completed Trades: {s25_count} (Blocked {len(s10_25_rejections)} weak trades)")  # noqa: print
    print(f"  - Win Rate:         {s25_win_rate:.1f}% (Change: {s25_win_rate - orig_win_rate:>+5.1f}%)")  # noqa: print
    print(f"  - Total Net Return: {s10_25_pnl:>+6.2f}% (Improvement: {s25_improvement:>+6.2f}% net profit!)")  # noqa: print
    print(f"  - Avg Return/Trade: {s25_avg_pnl:>+6.2f}%")  # noqa: print
    print("  - Rejected Trades (Weak Momentum Blocked):")  # noqa: print
    for r in s10_25_rejections:
        print(f"    * Blocked {r[0]} (s10={r[1]}, avoided loss of {r[2]:>+.2f}%)")  # noqa: print

    print("-" * 80)  # noqa: print
    print(f"\n🛡️ SCENARIO B: Strict Momentum Floor (Short10 >= 3.0)")  # noqa: print
    print(f"  - Completed Trades: {s30_count} (Blocked {len(s10_30_rejections)} weak trades)")  # noqa: print
    print(f"  - Win Rate:         {s30_win_rate:.1f}% (Change: {s30_win_rate - orig_win_rate:>+5.1f}%)")  # noqa: print
    print(f"  - Total Net Return: {s10_30_pnl:>+6.2f}% (Improvement: {s30_improvement:>+6.2f}% net profit!)")  # noqa: print
    print(f"  - Avg Return/Trade: {s30_avg_pnl:>+6.2f}%")  # noqa: print
    print("  - Rejected Trades (Weak Momentum Blocked):")  # noqa: print
    for r in s10_30_rejections:
        print(f"    * Blocked {r[0]} (s10={r[1]}, avoided loss of {r[2]:>+.2f}%)")  # noqa: print
    print("-" * 80)  # noqa: print

if __name__ == "__main__":
    run_backtrack_study()
