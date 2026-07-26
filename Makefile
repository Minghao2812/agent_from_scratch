ENV_NAME := agent-from-scratch

.PHONY: cp setup smoke ut clean-workspace

cp: # clean python cache
	find . -type d -name "__pycache__" -exec rm -rf {} +

setup: # setup environment
	conda env list | grep -q "^$(ENV_NAME) " || conda env create -f environment.yml
	conda env update -n $(ENV_NAME) -f environment.yml --prune
	conda run -n $(ENV_NAME) playwright install chromium
	test -f .env || cp .env.example .env
	@echo "环境已就绪，运行: conda activate $(ENV_NAME)"

co: # clean workspace output
	find workspace -mindepth 1 -maxdepth 1 \
		! -name '研报数据' \
		! -name 'demo_prompt_injection' \
		! -name '.gitkeep' \
		-exec rm -rf {} +
	mkdir -p workspace/output
	touch workspace/output/.gitkeep

smoke:
	pytest -m smoke -v

ut:
	pytest tests/unit -v
