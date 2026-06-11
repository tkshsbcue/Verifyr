.PHONY: help up up-emulator down logs build seed shell rebuild dev-backend dev-frontend

help:                ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up:                  ## Build + run the app (http://localhost:8000)
	docker compose up --build -d
	@echo "Verifyr is starting on http://localhost:8000"

up-emulator:         ## Run app + bundled Android emulator (Linux + /dev/kvm only)
	docker compose --profile emulator up --build -d

down:                ## Stop and remove containers
	docker compose down

logs:                ## Tail app logs
	docker compose logs -f app

build:               ## Build the image only
	docker compose build

seed:                ## Import sample checks for a Supabase user (USER_ID=<uuid> [CHECKS=checks.json])
	docker compose exec app python -m server.seed --checks $(or $(CHECKS),checks.json) --user-id $(USER_ID)

shell:               ## Open a shell in the app container
	docker compose exec app sh

# --- local (non-Docker) dev convenience ---
dev-backend:         ## Run the API locally from backend/
	cd backend && uvicorn server.main:app --reload --port 8000

dev-frontend:        ## Run the Vite dev server
	cd frontend && npm run dev
