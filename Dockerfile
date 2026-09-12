# AGMCIS(Master Prompt 第八十二節)。
#
# ⚠️ 目前的 Production 跑在 DigitalOcean 的 systemd 上,**不是 Docker**。
#    第八十二節同時也說「不要破壞目前正在運作的 Production」,
#    所以這個 Dockerfile 是為了讓開發、測試與 staging 環境可重現,
#    不是為了取代還在跑的東西。要切換必須是一次有計畫的遷移。

FROM python:3.11-slim AS base

# 不寫 .pyc、不緩衝 stdout。後者很重要:緩衝會讓 log 在容器被殺掉時
# 整批消失,而那通常正是你最需要那些 log 的時候。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先只複製需求檔:程式碼變動不該讓套件層的快取失效。
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 不用 root 跑。一個以 root 跑的交易程式,任何一個遠端執行漏洞
# 都會變成整台機器的問題。
RUN useradd --create-home --uid 10001 agmcis \
    && chown -R agmcis:agmcis /app
USER agmcis

EXPOSE 8000

# 健康檢查打 /health —— 它不需要金鑰,而且 unhealthy 時回 503,
# 所以 Docker / 負載平衡器不需要解析 JSON 就知道狀態。
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status == 200 else 1)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]


# ---- 排程器 ----
#
# 排程與 Web 分開跑。同一個容器裡跑兩個東西的話,重啟 Web
# 會順便打斷排程,而排程正在跑的可能是一次對帳。
FROM base AS scheduler
CMD ["python", "-m", "agmcis.scheduling.runner"]
