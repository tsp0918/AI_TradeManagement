from sqlalchemy.orm import DeclarativeBase


class ScreeningBase(DeclarativeBase):
    """screening モジュール専用の SQLAlchemy 基底クラス。テーブル名は scr_ プレフィックス。"""
    pass
