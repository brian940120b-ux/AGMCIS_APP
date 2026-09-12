# HTTPS(Master Prompt 第八十二節)

## 為什麼這個系統一定要 HTTPS

儀表板的金鑰走 cookie。HTTP 的話,那個 cookie 在每一次請求裡都是
明文,而任何在同一段網路上的人都拿得到 —— 拿到之後他看得到你的
持倉、你的風控參數,以及 `/api/live_gate` 的狀態。

而且 `web_auth.py` 的 cookie 設了 `Secure` flag,那代表**瀏覽器在
HTTP 下根本不會送出它** —— 沒有 HTTPS 的話你會一直被登出,
而那個症狀看起來像認證壞掉。

## 步驟

```
sudo apt install certbot python3-certbot-nginx
sudo mkdir -p /var/www/certbot
```

先把 `deploy/nginx.conf` 裡的 `YOUR_DOMAIN` 換成你的網域,
以及兩處 `server_name _;`。**沒有網域就不能用 Let's Encrypt** ——
它不簽 IP。

```
sudo cp deploy/nginx.conf /etc/nginx/sites-available/agmcis
sudo ln -sf /etc/nginx/sites-available/agmcis /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

sudo certbot --nginx -d your.domain
```

## 自動更新

certbot 裝好時會自己建一個 systemd timer。確認它在:

```
systemctl list-timers | grep certbot
```

**確認那個 timer 真的在跑。** 憑證九十天過期,而過期的那一天,
瀏覽器會擋下整個儀表板 —— 通常發生在你三個月前設定完、
早就忘記這件事的時候。

`deploy/nginx.conf` 的 HTTP server 段留了 `/.well-known/acme-challenge/`
不導向 HTTPS。那一段被導走的話,更新會永遠失敗。

## 驗證

```
curl -sI https://your.domain/health | head -1
```

應該回 `HTTP/2 200`。回 503 代表系統本身不健康(那是另一個問題,
但至少 HTTPS 是通的)。
