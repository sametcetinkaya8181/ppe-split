"""Sunucu tarafı bağımsız maskeleme (dedektör maskelemesine güvenilmez). URL kullanıcı bilgisi, hassas sorgu
parametreleri ve fragment gizlenir/atılır."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_KEYS = {"password", "passwd", "pass", "pwd", "user", "username", "login", "token", "access_token",
                  "auth", "authorization", "key", "apikey", "api_key", "secret", "signature", "sig", "credential",
                  "credentials", "session", "sessionid"}
_URL_USERINFO = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/\s@]+@")
_QUERY_SECRET = re.compile(r"([?&](?:%s)=)[^&\s#]*" % "|".join(sorted(SENSITIVE_KEYS, key=len, reverse=True)),
                           re.IGNORECASE)
_FRAGMENT = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.\-]*://\S*?)#\S*")


def redact_source(source) -> str:
    s = str(source)
    p = urlsplit(s)
    if len(p.scheme) <= 1 or not p.netloc:          # dosya yolu / kamera indeksi
        return redact_text(s)
    netloc = p.netloc
    if "@" in netloc:
        netloc = "***@" + netloc.rsplit("@", 1)[1]
    query = p.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        query = urlencode([(k, "***" if k.lower() in SENSITIVE_KEYS else v) for k, v in pairs], safe="*")
    return urlunsplit((p.scheme, netloc, p.path, query, ""))


def redact_text(text) -> str:
    s = _URL_USERINFO.sub(r"\1***@", str(text))
    s = _QUERY_SECRET.sub(r"\1***", s)
    return _FRAGMENT.sub(r"\1", s)
