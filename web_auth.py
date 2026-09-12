"""
Web / API 存取控制。

Phase 0.5 修正兩個問題:
  1. 全部 /api/* 端點原本沒有任何驗證 —— 帳戶餘額、所有持倉與交易紀錄公開可讀,
     而且 GET /api/auto_trader 公開可寫(會實際開倉)。
  2. Dashboard 的金鑰走 URL query string(/?key=...),會進 Nginx access log、
     瀏覽器歷史與 Referer。

做法:
  - 金鑰驗證通過後發一個 HttpOnly cookie,並把使用者導回沒有 query string 的網址。
  - API 依序接受 cookie、X-AGMCIS-KEY header、?key=(保留給 curl 與既有腳本)。
  - 金鑰比對使用 secrets.compare_digest,避免時序側通道。
  - DASHBOARD_KEY 未設定時一律拒絕,不再 fallback 到寫死的預設值。
"""
import logging
import secrets

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse

from agmcis.config import settings

logger = logging.getLogger("agmcis.web_auth")

COOKIE_NAME = settings.SESSION_COOKIE_NAME
HEADER_NAME = settings.API_KEY_HEADER
COOKIE_MAX_AGE = settings.SESSION_COOKIE_MAX_AGE


def dashboard_key():
    """未設定就回傳空字串;空金鑰代表「拒絕所有人」而不是「放行所有人」。"""
    return settings.dashboard_key()


def key_is_valid(candidate) -> bool:
    expected = dashboard_key()
    if not expected or not candidate:
        return False
    return secrets.compare_digest(str(candidate), expected)


def extract_key(request: Request):
    """依 cookie -> header -> query 的順序找金鑰。"""
    return (
        request.cookies.get(COOKIE_NAME)
        or request.headers.get(HEADER_NAME)
        or request.query_params.get("key")
    )


def is_authenticated(request: Request) -> bool:
    return key_is_valid(extract_key(request))


def require_api_key(request: Request):
    """
    FastAPI dependency。掛在每個 router 上,未通過就 401。

    用法:
        app.include_router(some_router, dependencies=[Depends(require_api_key)])
    """
    if not dashboard_key():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DASHBOARD_KEY 未設定,API 已停用。請先在 .env 設定金鑰。",
        )

    if not is_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未授權。請提供 X-AGMCIS-KEY header,或先登入 Dashboard 取得 session。",
        )

    return True


def set_session_cookie(response, key):
    secure = settings.SESSION_COOKIE_SECURE
    response.set_cookie(
        COOKIE_NAME,
        key,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    return response


def redirect_with_session(target_path, key, request=None):
    """驗證通過後把金鑰放進 cookie 並導回乾淨網址,讓金鑰不留在瀏覽紀錄裡。"""
    _audit_login(request, target_path)
    response = RedirectResponse(url=target_path, status_code=status.HTTP_303_SEE_OTHER)
    return set_session_cookie(response, key)


def _audit_login(request, target_path):
    """
    登入稽核(第六十五節)。

    只記來源 IP 與目標頁面 —— **不記金鑰本身,也不記它的任何片段**。
    第十節說得很明白:API Key 不進 Database。一個「只記前四碼」的
    折衷做法在這裡沒有價值,卻讓金鑰有了第二個存在的地方。

    稽核失敗不擋登入。這一層是觀測,不是門禁。
    """
    try:
        from agmcis.review.decision_log import audit

        source_ip = None
        if request is not None and getattr(request, "client", None):
            source_ip = request.client.host

        audit("LOGIN", actor="dashboard", target=target_path,
              source_ip=source_ip, detail="Dashboard 金鑰驗證通過")
    except Exception:
        logger.exception("登入稽核寫入失敗")


LOGIN_PAGE = """<html><head><meta charset='utf-8'><title>AGMCIS</title></head>
<body style='background:#0c1213;color:#e3eaea;font-family:system-ui,sans-serif;padding:48px;line-height:1.6'>
<h1 style='margin:0 0 12px'>AGMCIS Protected</h1>
<p style='color:#9dacae;margin:0 0 24px'>需要存取金鑰。</p>
<form method='get' action='' style='display:flex;gap:8px;max-width:420px'>
  <input type='password' name='key' placeholder='存取金鑰' autofocus
         style='flex:1;padding:10px 12px;background:#141c1e;color:#e3eaea;border:1px solid #263234;border-radius:3px'>
  <button type='submit'
          style='padding:10px 18px;background:#5badbc;color:#0c1213;border:0;border-radius:3px;font-weight:600;cursor:pointer'>
    進入
  </button>
</form>
<p style='color:#78878a;font-size:13px;margin-top:24px'>
  金鑰驗證後會存進 HttpOnly cookie,不會留在網址列。
</p>
</body></html>"""
