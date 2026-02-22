from sqlalchemy.orm import DeclarativeBase, declared_attr


class PlatformBase(DeclarativeBase):
    """platform-core 全モデルの基底クラス。"""

    @declared_attr.directive
    def __tablename__(cls) -> str:
        # クラス名をスネークケースに変換してテーブル名にする
        # 例: TenantUser -> tenant_user
        import re
        name = re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower()
        return f"plat_{name}"
