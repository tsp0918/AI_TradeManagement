from sqlalchemy import Column, Integer, String, Text, DateTime
from datetime import datetime
from app.database import Base


class JplatpatPatent(Base):
    """
    J-Platpat API から取得した特許情報を格納するテーブル。

    Phase 1: JSON メタ情報（IPC・Fターム・出願人など）
    Phase 2: 明細書・請求項 XML（claims_xml / description_xml）
    """

    __tablename__ = "jplatpat_patents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)

    # ── キー ──────────────────────────────────────────────────────────
    app_number      = Column(String(20), unique=True, nullable=False, index=True)
    # ^ 正規化済み出願番号 (例: "2020008423")
    app_number_raw  = Column(String(50), nullable=True)
    # ^ CSV から読んだ元の出願番号文字列

    # ── 基本情報 ─────────────────────────────────────────────────────
    title           = Column(Text, nullable=True)
    applicants      = Column(Text, nullable=True)   # JSON list of str
    inventors       = Column(Text, nullable=True)   # JSON list of str
    filing_date     = Column(String(10), nullable=True)  # YYYY-MM-DD
    publication_number = Column(String(30), nullable=True)
    publication_date   = Column(String(10), nullable=True)
    registration_number = Column(String(30), nullable=True)
    registration_date   = Column(String(10), nullable=True)
    app_status      = Column(String(100), nullable=True)  # 最新審査ステータス

    # ── 分類情報 ─────────────────────────────────────────────────────
    ipc_codes       = Column(Text, nullable=True)   # JSON list of str
    fi_codes        = Column(Text, nullable=True)   # JSON list of str
    f_terms         = Column(Text, nullable=True)   # JSON list of str
    registration_ipc = Column(Text, nullable=True)  # JSON list (registration_info から)

    # ── 登録追加情報 ──────────────────────────────────────────────────
    right_persons   = Column(Text, nullable=True)   # JSON list（権利者名）
    number_of_claims = Column(String(10), nullable=True)  # 請求項数
    expire_date     = Column(String(10), nullable=True)   # 存続期間満了日

    # ── 利用可能書類 ─────────────────────────────────────────────────
    available_docs  = Column(Text, nullable=True)   # JSON list（明細書・請求項など）

    # ── 引用文献 ─────────────────────────────────────────────────────
    cited_docs      = Column(Text, nullable=True)   # JSON list

    # ── J-PlatPat リンク ─────────────────────────────────────────────
    jpp_url         = Column(String(500), nullable=True)

    # ── 生 JSON（フルレスポンス保存）───────────────────────────────────
    progress_json      = Column(Text, nullable=True)     # app_progress raw
    registration_json  = Column(Text, nullable=True)     # registration_info raw

    # ── Google Patents 補完データ ────────────────────────────────────
    abstract_ja     = Column(Text, nullable=True)        # 要約（日本語）
    claims_xml      = Column(Text, nullable=True)        # 請求項テキスト
    description_xml = Column(Text, nullable=True)        # 明細書テキスト（先頭8000字）
    google_doc_id   = Column(String(30), nullable=True)  # JP2019041059A 等
    full_text_fetched_at = Column(DateTime, nullable=True)

    # ── フェッチ管理 ─────────────────────────────────────────────────
    fetch_status    = Column(String(20), default="pending", nullable=False)
    # ^ pending / fetching / completed / failed / partial
    fetch_error     = Column(Text, nullable=True)
    api_fetched_at  = Column(DateTime, nullable=True)

    # ── メタ ─────────────────────────────────────────────────────────
    imported_at     = Column(DateTime, default=datetime.utcnow, nullable=False)
    note            = Column(Text, nullable=True)       # ユーザーメモ

    def __repr__(self):
        return (
            f"<JplatpatPatent(id={self.id}, app_number='{self.app_number}', "
            f"title='{(self.title or '')[:40]}', status='{self.fetch_status}')>"
        )
