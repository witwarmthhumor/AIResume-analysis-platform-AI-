r"""db/session 纯函数直测（unit-test 技能补测：v4.5.1 新增、此前零覆盖）。

- graph_dsn：LangGraph checkpoint 连接串——协议替换 / search_path 注入 /
  已带 query 时的 & 追加（S6-2 防御分支）；
- tools/common._escape_like：ILIKE 通配符转义（S6-3）——% _ \ 三个字面量。

两处都是无副作用的纯函数：monkeypatch settings 即可，不连库、不烧额度。
"""

import pytest

from app.core.config import settings
from app.db.session import graph_dsn
from app.services.agent.tools.common import _escape_like


@pytest.fixture
def db_url(monkeypatch):
    """临时改写 database_url，出测还原（settings 是模块级单例）。"""

    def _set(url: str) -> None:
        monkeypatch.setattr(settings, "database_url", url)

    return _set


class TestGraphDsn:
    def test_plain_url_gets_search_path(self, db_url):
        """正常路径：+psycopg 驱动名替换为裸协议，search_path 作为唯一 query 注入。"""
        db_url("postgresql+psycopg://ai:ai@127.0.0.1:5432/ai_interview")
        assert graph_dsn() == (
            "postgresql://ai:ai@127.0.0.1:5432/ai_interview"
            "?options=-csearch_path%3Dlanggraph%2Cpublic"
        )

    def test_existing_query_joined_with_ampersand(self, db_url):
        """S6-2 防御分支：URL 已带 query 时用 & 追加，绝不出双问号的非法 DSN。"""
        db_url(
            "postgresql+psycopg://ai:ai@db.internal:5432/ai_interview?sslmode=require"
        )
        dsn = graph_dsn()
        assert dsn == (
            "postgresql://ai:ai@db.internal:5432/ai_interview"
            "?sslmode=require&options=-csearch_path%3Dlanggraph%2Cpublic"
        )
        assert dsn.count("?") == 1  # 双 ? 会直接被 psycopg 拒绝

    def test_query_with_trailing_ampersand_still_wellformed(self, db_url):
        """边界：已带 query 且以 & 结尾——追加后不产生连续 &&。"""
        db_url(
            "postgresql+psycopg://ai:ai@db.internal:5432/ai_interview?connect_timeout=5&"
        )
        dsn = graph_dsn()
        assert "connect_timeout=5&options=-csearch_path" in dsn
        assert "&&" not in dsn

    def test_fragment_is_preserved(self, db_url):
        """边界：URL 带 fragment（极罕见）——重组后原样保留、不进 query。"""
        db_url("postgresql+psycopg://ai:ai@127.0.0.1:5432/ai_interview#frag")
        dsn = graph_dsn()
        assert dsn.endswith("#frag")
        assert "search_path" in dsn.split("#")[0]


class TestEscapeLike:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("RAG", "RAG"),  # 无特殊字符：原样
            ("100%", r"100\%"),  # % → 字面
            ("a_b", r"a\_b"),  # _ → 字面
            ("a\\b", r"a\\b"),  # 反斜杠先转义，防破坏转义序列
            ("%_\\", r"\%\_\\"),  # 混合：\ 先翻倍，% _ 各加前缀
            ("", ""),  # 空串原样
        ],
    )
    def test_escape_table(self, raw, expected):
        assert _escape_like(raw) == expected

    def test_round_trip_via_pg_semantics(self):
        """转义后的模式在 PG LIKE 语义下等价于字面包含（不经库，验转义序列形状）。"""
        escaped = _escape_like("a%b_c")
        assert escaped == r"a\%b\_c"  # 反斜杠前缀恰好落在两个通配符前
        assert "\\\\" not in escaped.replace("\\\\", "")  # 无意外双转义
