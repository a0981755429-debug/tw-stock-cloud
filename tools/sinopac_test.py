"""永豐 API 首次使用的「模擬測試」：模擬環境登入 + 下一張模擬單（不會真的成交、不花錢）。

在 GitHub Actions 手動執行「永豐 API 模擬測試」。需要 Secrets：SJ_TEST_API_KEY、SJ_TEST_SECRET_KEY
（一把勾了「交易」權限的臨時 Key，測完請到永豐刪掉，並刪掉這兩個 Secret）。
永豐規定：週一到週五 08:00–18:00 才收測試報告（18:00–20:00 只收台灣 IP），且要先簽署條款。
公開 log 只印成功或失敗，不印帳號。
"""
import os
import re
import sys
import time

import shioaji as sj


def main() -> int:
    key = os.environ.get("SJ_TEST_API_KEY") or os.environ.get("SJ_API_KEY")
    sec = os.environ.get("SJ_TEST_SECRET_KEY") or os.environ.get("SJ_SECRET_KEY")
    if not key or not sec:
        print("❌ 沒有設定 SJ_TEST_API_KEY / SJ_TEST_SECRET_KEY")
        return 1
    api = sj.Shioaji(simulation=True)
    try:
        api.login(api_key=key, secret_key=sec)
        print("✅ 模擬環境登入成功")
        print("下載商品檔中…")
        try:
            api.fetch_contracts(contract_download=True, contracts_timeout=180000)
        except Exception as e:  # noqa: BLE001
            print(f"商品檔下載逾時（{type(e).__name__}），繼續等待…")
        contract = None
        for _ in range(30):
            try:
                contract = api.Contracts.Stocks["2890"]
            except Exception:  # noqa: BLE001
                contract = None
            if contract is not None:
                break
            time.sleep(3)
        if contract is None:
            print("❌ 拿不到 2890 商品資料，請稍後再試")
            return 1
        print("商品檔 OK，送出模擬單…")
        order = sj.StockOrder(action=sj.Action.Buy, price=contract.reference or 20, quantity=1,
                              price_type=sj.StockPriceType.LMT, order_type=sj.OrderType.ROD,
                              order_lot=sj.StockOrderLot.Common, order_cond=sj.StockOrderCond.Cash,
                              account=api.stock_account)
        trade = api.place_order(contract, order, timeout=60000)
        status = str(getattr(trade.status, "status", "")).split(".")[-1]
        print(f"模擬下單狀態：{status}")
        if status in ("PendingSubmit", "PreSubmitted", "Submitted", "Filled"):
            print("✅ 證券模擬下單測試完成。約 5 分鐘後永豐審核通過，就可以刪掉臨時 Key。")
            return 0
        print("❌ 模擬下單沒有成功，請確認已簽署條款、Key 有勾「交易」，且在週一到週五 08:00–18:00 執行")
        return 1
    except Exception as e:  # noqa: BLE001
        msg = re.sub(r"[A-Z]?\d{6,}", "***", str(e))[:150]
        print(f"❌ 測試失敗：{type(e).__name__} {msg}（請確認已簽署條款、Key 有勾「交易」，且在週一到週五 08:00–18:00 執行）")
        return 1
    finally:
        try:
            api.logout()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
