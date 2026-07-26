from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    String, Integer, DateTime, ForeignKey, JSON, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class DapApp(Base):
    __tablename__ = "dap_apps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    pages: Mapped[List["DapPage"]] = relationship("DapPage", back_populates="app", cascade="all, delete-orphan")
    rules: Mapped[List["DapRule"]] = relationship("DapRule", back_populates="app", cascade="all, delete-orphan")
    releases: Mapped[List["DapRelease"]] = relationship("DapRelease", back_populates="app", cascade="all, delete-orphan")


class DapPage(Base):
    __tablename__ = "dap_pages"
    __table_args__ = (UniqueConstraint("app_id", "page_key", name="uq_page_app_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("dap_apps.id"), index=True)

    page_key: Mapped[str] = mapped_column(String(80), index=True)   # e.g. rd_case_form
    name: Mapped[str] = mapped_column(String(200), default="")
    url_regex: Mapped[str] = mapped_column(String(600))
    must_have: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    app: Mapped["DapApp"] = relationship("DapApp", back_populates="pages")
    targets: Mapped[List["DapTarget"]] = relationship("DapTarget", back_populates="page", cascade="all, delete-orphan")
    interventions: Mapped[List["DapIntervention"]] = relationship("DapIntervention", back_populates="page", cascade="all, delete-orphan")


class DapTarget(Base):
    __tablename__ = "dap_targets"
    __table_args__ = (UniqueConstraint("page_id", "target_key", name="uq_target_page_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("dap_pages.id"), index=True)

    target_key: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    anchors: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    page: Mapped["DapPage"] = relationship("DapPage", back_populates="targets")


class DapRule(Base):
    __tablename__ = "dap_rules"
    __table_args__ = (UniqueConstraint("app_id", "rule_key", name="uq_rule_app_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("dap_apps.id"), index=True)

    rule_key: Mapped[str] = mapped_column(String(120), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    rule_type: Mapped[str] = mapped_column(String(60))  # text_quality | missing_or_generic | any_of_targets_missing
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(20), default="medium")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    app: Mapped["DapApp"] = relationship("DapApp", back_populates="rules")


class DapIntervention(Base):
    __tablename__ = "dap_interventions"
    __table_args__ = (UniqueConstraint("page_id", "intervention_key", name="uq_iv_page_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("dap_pages.id"), index=True)

    intervention_key: Mapped[str] = mapped_column(String(140), index=True)
    name: Mapped[str] = mapped_column(String(220), default="")

    trigger: Mapped[dict] = mapped_column(JSON)   # {"type":"field_blur","target_id":"t_enduse"} etc
    rule_key: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    block_action: Mapped[int] = mapped_column(Integer, default=0)  # 0/1
    coach: Mapped[dict] = mapped_column(JSON, default=dict)
    actions: Mapped[list] = mapped_column(JSON, default=list)      # ordered actions

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    page: Mapped["DapPage"] = relationship("DapPage", back_populates="interventions")


class DapRelease(Base):
    __tablename__ = "dap_releases"
    __table_args__ = (UniqueConstraint("app_id", "env", "version", name="uq_release_app_env_ver"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("dap_apps.id"), index=True)

    env: Mapped[str] = mapped_column(String(20), default="local")
    version: Mapped[str] = mapped_column(String(40), default="0.1.0")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft/published
    runtime_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    app: Mapped["DapApp"] = relationship("DapApp", back_populates="releases")


class DapEvent(Base):
    __tablename__ = "dap_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[str] = mapped_column(String(40), default="")
    app_key: Mapped[str] = mapped_column(String(80), index=True)
    page_id: Mapped[str] = mapped_column(String(80), default="")
    user_pseudo_id: Mapped[str] = mapped_column(String(120), default="")
    event_type: Mapped[str] = mapped_column(String(40))
    event_name: Mapped[str] = mapped_column(String(120))
    context: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DapChatConfig(Base):
    """チャットウィジェットのモジュール別設定（有効/無効、プロンプト補足）"""
    __tablename__ = "dap_chat_configs"

    port: Mapped[str] = mapped_column(String(8), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), default="")
    enabled: Mapped[int] = mapped_column(Integer, default=1)   # 1=有効 0=無効
    prompt_supplement: Mapped[str] = mapped_column(String(2000), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DapWorkflowSession(Base):
    """DAP ワークフロー伴走セッション。

    ユーザーが UC（ユースケース）を開始するとセッションが作成され、
    ステップ完了ごとに current_step が進む。
    context には UC 進行中に収集した情報（item_id, eccn, dest_country 等）を保存する。
    """
    __tablename__ = "dap_workflow_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # チャットセッション ID（dap_chat_session と紐づけ）
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # UC識別子: "UC1"〜"UC10"
    uc_id: Mapped[str] = mapped_column(String(8), nullable=False)
    # uc の表示名（例: "新規品目の輸出審査"）
    uc_title: Mapped[str] = mapped_column(String(100), default="")
    # 現在のステップ番号（1始まり）
    current_step: Mapped[int] = mapped_column(Integer, default=1)
    # 総ステップ数
    total_steps: Mapped[int] = mapped_column(Integer, default=5)
    # 完了済みステップ番号リスト（JSON array: [1, 2, 3]）
    completed_steps: Mapped[list] = mapped_column(JSON, default=list)
    # UC 進行中に収集した業務情報（item_id, eccn, dest_country 等）
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    # ステータス: "active" / "completed" / "abandoned"
    status: Mapped[str] = mapped_column(String(20), default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_active_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DapChatLog(Base):
    """チャット会話ログ。セッションID単位で全ターンを記録する。"""
    __tablename__ = "dap_chat_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    turn_num: Mapped[int] = mapped_column(Integer, default=0)
    user_message: Mapped[str] = mapped_column(String(8000), default="")
    assistant_reply: Mapped[str] = mapped_column(String(8000), default="")
    # ページ/モジュールコンテキスト（port, page_path, module_key 等）
    page_context: Mapped[dict] = mapped_column(JSON, default=dict)
    # レスポンスで返されたアクション (highlight / navigate_to 等)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    # ヒアリングモード中の場合のモード名
    intake_mode: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DapSession(Base):
    """セッション永続化テーブル。_SESSION_STORE の write-through ターゲット。"""
    __tablename__ = "dap_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    history: Mapped[list] = mapped_column(JSON, default=list)
    task: Mapped[str] = mapped_column(String(500), default="")
    persona: Mapped[dict] = mapped_column(JSON, default=dict)
    intake_state: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    last_access: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
