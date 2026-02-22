.PHONY: run test revision upgrade

run:
	uvicorn app.main:app --reload

test:
	pytest -q

revision:
	alembic revision --autogenerate -m "$(m)"

upgrade:
	alembic upgrade head
