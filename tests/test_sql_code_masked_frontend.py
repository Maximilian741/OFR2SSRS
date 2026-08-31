"""Every SQL front-end pass must search the CODE-MASKED text.

Wild-corpus upload-fatal class: a leading ``--`` comment that merely
CONTAINS the word "select" used to win ``find("SELECT")`` in
``_alias_select_items``; the SELECT-list window then opened inside the
COMMENT, the bare-star guard could no longer match, and the emitter
shipped ``select * AS <comment-derived-name> from(...`` — ORA-00923 at
upload (the project-fatal error class). The same unmasked keyword search
lived in ``subreports._select_columns`` (phantom columns invented from
comment words) and in the bind/lexical token scans.

These tests pin the contract: comments and string literals can never
steer keyword/token detection, and a derived alias never contains
comment words. All shapes are synthetic — no customer text.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from converter.generators.rdl import (  # noqa: E402
    _alias_select_items,
    _blank_sql_comments,
    _detect_oracle_bind_vars,
    _detect_query_parameters,
    _make_ssrs_oracle_compatible,
)
from converter.subreports import (  # noqa: E402
    _bind_params_in_sql,
    _lexical_refs_in_sql,
    _select_columns,
    _trim_to_first_statement,
)


# ---------------------------------------------------------------------------
# The upload-fatal shape itself: leading comment containing "select",
# then a bare-star select. The star must stay UNALIASED.
# ---------------------------------------------------------------------------

def test_leading_comment_select_does_not_hijack_the_select_window():
    sql = ("--select the top items overview\n"
           "select *\n"
           "from( select a as \"Spaced Alias\", b from t )")
    out = _alias_select_items(sql, ["Spaced_Alias", "b"])
    # The star can NEVER take an alias (ORA-00923); with the comment
    # hijack the old code emitted "select * AS THE_TOP_ITEMS_OVERVIEW_SEL".
    assert "* AS" not in out.upper().replace("*  AS", "* AS")
    assert out == sql, "bare-star select must pass through unchanged"


def test_derived_alias_never_contains_comment_words():
    sql = ("SELECT /* secretnote alpha */ UPPER(x), y --tailnote beta\n"
           "FROM t")
    out = _alias_select_items(sql, ["UPPER_X", "y"])
    assert "SECRETNOTE" not in out.upper().replace("SECRETNOTE ALPHA */", "")
    # the expression still gets its normal derived alias
    assert "UPPER(x) AS UPPER_X" in out
    # comments themselves survive verbatim (they are legal SQL)
    assert "/* secretnote alpha */" in out and "--tailnote beta" in out


def test_select_keyword_inside_string_literal_and_identifier_is_ignored():
    # "selected_flag" (identifier containing SELECT) + a literal containing
    # "select ... from" must not open the SELECT-list window early.
    sql = ("--select comment decoy\n"
           "SELECT 'select x from y' AS c1, t.selected_flag FROM t")
    out = _alias_select_items(sql, ["c1", "selected_flag"])
    assert out == sql, "aliased literal + bare column need no rewrite"


def test_star_with_table_prefix_stays_unaliased_behind_comment():
    sql = "/* select all rows */ select o.* from orders o"
    out = _alias_select_items(sql, ["col1"])
    assert out == sql


# ---------------------------------------------------------------------------
# subreports._select_columns: phantom columns from comment words
# ---------------------------------------------------------------------------

def test_select_columns_never_invents_columns_from_comment_words():
    sql = ("--select phantom_col from junk_comment\n"
           "select real_col from t")
    assert _select_columns(sql) == ["real_col"]


def test_select_columns_ignores_from_inside_string_literal():
    sql = "select 'text from nowhere' as c1, other_col from t"
    assert _select_columns(sql) == ["c1", "other_col"]


def test_trim_to_first_statement_skips_comment_select():
    blob = ("-- select overview of the extract\n"
            "some artifact prose\n"
            "SELECT a FROM t;\n"
            "BEGIN null; END;")
    out = _trim_to_first_statement(blob)
    assert out == "SELECT a FROM t"


# ---------------------------------------------------------------------------
# Bind / parameter / lexical token scans
# ---------------------------------------------------------------------------

def test_bind_detection_skips_comments_and_literals():
    sql = ("-- set :P_FAKE before running\n"
           "SELECT ' :P_LIT ' FROM t WHERE x = :P_REAL")
    assert _detect_oracle_bind_vars(sql) == ["P_REAL"]
    assert _bind_params_in_sql(sql) == ["P_REAL"]


def test_query_parameter_detection_skips_comments_and_literals():
    tsql = "-- uses @P_FAKE\nSELECT ' @P_LIT ' FROM t WHERE x = @P_REAL"
    assert _detect_query_parameters(tsql) == ["P_REAL"]


def test_lexical_refs_skip_literals_and_comments():
    sql = ("-- mentions &P_FAKE in prose\n"
           "SELECT 'AT&T' AS co FROM t WHERE 1=1 &P_REAL_WHERE")
    assert _lexical_refs_in_sql(sql) == ["P_REAL_WHERE"]


def test_to_date_wrap_never_rewrites_literal_or_comment_text():
    sql = ("-- :P_DT note\n"
           "SELECT ' :P_DT ' FROM t WHERE d = :P_DT")
    out = _make_ssrs_oracle_compatible(sql, {"P_DT": "DateTime"})
    # exactly ONE wrap: the real code-position bind
    assert out.count("TO_DATE(:P_DT, 'YYYY-MM-DD')") == 1
    assert "-- :P_DT note" in out          # comment untouched
    assert "' :P_DT '" in out              # literal untouched


# ---------------------------------------------------------------------------
# The masker itself
# ---------------------------------------------------------------------------

def test_blank_sql_comments_is_length_preserving_and_blanks_comments():
    sql = "a /*x*/ b --y\nc 'lit' d \"Quoted Id\""
    det = _blank_sql_comments(sql)
    assert len(det) == len(sql)
    assert "x" not in det.split("'")[0][:8]      # block comment blanked
    assert "--" not in det and "/*" not in det
    assert "'lit'" in det                        # literals kept by default
    assert '"Quoted Id"' in det                  # quoted identifiers kept


def test_blank_sql_comments_literal_mode_keeps_quotes_blanks_content():
    det = _blank_sql_comments("x 'se''cret' y", blank_literals=True)
    assert len(det) == len("x 'se''cret' y")
    assert "secret" not in det.replace(" ", "")
    assert det.count("'") == 2                   # delimiters survive


def test_blank_sql_comments_unterminated_quote_is_not_a_literal():
    # docx prose apostrophe: blanking to EOF would erase the keywords the
    # caller is searching for.
    prose = "the report's SELECT a FROM t"
    det = _blank_sql_comments(prose, blank_literals=True)
    assert "SELECT a FROM t" in det
