// 證交所即時報價轉接站（Cloudflare Workers 免費方案）
// 用途：證交所 mis.twse.com.tw 不讓別的網頁直接讀資料，這支小程序幫你的網頁去拿，再回傳給網頁。
// 安全：只接受股票代號格式的查詢、只轉給證交所，不能拿來連其他網站；不經手任何持股資料。
// 設定方式見 README「網頁即時股價」一節。

const ALLOW_ORIGIN = "https://a0981755429-debug.github.io";   // 只讓你的網頁使用

export default {
  async fetch(request) {
    const cors = {
      "Access-Control-Allow-Origin": ALLOW_ORIGIN,
      "Access-Control-Allow-Methods": "GET",
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
    };
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });

    const ex = new URL(request.url).searchParams.get("ex_ch") || "";
    // 例：tse_2330.tw|otc_6488.tw，最多 60 檔
    if (!/^((tse|otc)_[0-9A-Z]{4,6}\.tw\|?){1,60}$/.test(ex)) {
      return new Response('{"error":"bad ex_ch"}', { status: 400, headers: cors });
    }
    const url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?json=1&delay=0&ex_ch=" + encodeURIComponent(ex) + "&_=" + Date.now();
    try {
      const r = await fetch(url, {
        headers: {
          "Referer": "https://mis.twse.com.tw/stock/index.jsp",
          "User-Agent": "Mozilla/5.0",
          "Accept": "application/json",
        },
      });
      return new Response(await r.text(), { status: r.status, headers: cors });
    } catch (e) {
      return new Response('{"error":"upstream"}', { status: 502, headers: cors });
    }
  },
};
