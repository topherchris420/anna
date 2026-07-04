# Makefile for Anna's Archive development

.PHONY: help install test test-unit test-integration test-coverage lint format clean run

# Default target
help:
	@echo "Anna's Archive - Development Commands"
	@echo ""
	@echo "Development:"
	@echo "  make install      - Install dependencies"
	@echo "  make run          - Run the application"
	@echo ""
	@echo "Testing:"
	@echo "  make test         - Run all tests"
	@echo "  make test-unit    - Run unit tests only"
	@echo "  make test-integration - Run integration tests"
	@echo "  make test-coverage   - Run tests with coverage"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint         - Run linting"
	@echo "  make format       - Format code"
	@echo ""
	@echo "Data Imports:"
	@echo "  make import-all   - Import all data sources"
	@echo "  make import-zlib  - Import Z-Library data"
	@echo ""
	@echo "Database:"
	@echo "  make db-reset     - Reset database"
	@echo "  make db-status    - Show database status"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean        - Clean up generated files"

# Installation
install:
	./run pip install -r requirements.txt
	./run pip install -r requirements-dev.txt

# Run the application
run:
	./run up

# Testing
test:
	./run pytest

test-unit:
	./run pytest -m "not integration"

test-integration:
	./run pytest -m "integration"

test-coverage:
	./run pytest --cov=allthethings --cov-report=html --cov-report=term-missing

test-quick:
	./run pytest -x -q

# Linting
lint:
	./run flake8 allthethings test data-imports lib
	./run black --check allthethings test data-imports lib

format:
	./run black allthethings test data-imports lib
	./run isort allthethings test data-imports lib

# Data imports
import-all:
	./run flask cli import_all

import-zlib:
	./run flask cli import_zlib

import-libgen-rs:
	./run flask cli import_libgen_rs

import-libgen-li:
	./run flask cli import_libgen_li

import-open-library:
	./run flask cli import_open_library

import-isbndb:
	./run flask cli import_isbndb

import-status:
	./run flask cli import_status

import-list:
	./run flask cli list_importers

# Database
db-reset:
	./run flask cli dbreset

db-mysql-md5:
	./run flask cli mysql_build_computed_all_md5s

db-elastic-reset:
	./run flask cli elastic_reset_md5_dicts

db-elastic-build:
	./run flask cli elastic_build_md5_dicts

# Cleanup
clean:
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf .pytest_cache
	rm -rf __pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -f .coverage*
	rm -f checkpoint_*.json
	rm -f imports.log