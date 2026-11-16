#!/usr/bin/env bash
# Init de la base de desarrollo (change add-docker-compose-dev-db, design D4).
#
# El entrypoint de la imagen oficial lo corre UNA sola vez, con el volumen de
# datos vacío, como el superusuario de bootstrap y contra el servidor temporal
# (solo socket Unix). Con datos presentes no se vuelve a ejecutar.
#
# Crea SOLO el rol de la app, sin privilegios, y las bases <app> y <app>_test
# con ese rol como dueño. Nada de tablas, esquemas ni extensiones: el esquema
# sale solo de Alembic (DAT-6).
#
# Funciona ejecutado o con `source` (en Windows el bit de ejecución del montaje
# no está garantizado): todo vive en una función, no hay `exit` a nivel tope y
# no cambia las opciones del shell del entrypoint. Ante error, la función
# devuelve 1 y el entrypoint aborta el arranque.
#
# Ningún valor se imprime ni pasa por la línea de comando: psql lee las
# variables del entorno con \getenv y las cita como identificador (:"x") o como
# literal (:'x'). No se arma SQL concatenando strings (SEC-4).

create_app_role_and_databases() {
	local identifier='^[a-z_][a-z0-9_]*$'

	if [[ ! "${POSTGRES_APP_USER:-}" =~ $identifier ]] || [ "${#POSTGRES_APP_USER}" -gt 63 ]; then
		echo "init: POSTGRES_APP_USER must match ${identifier} and have at most 63 characters." >&2
		return 1
	fi
	# 58 = 63 (límite de PostgreSQL) - 5 (sufijo _test de la gemela).
	if [[ ! "${POSTGRES_APP_DB:-}" =~ $identifier ]] || [ "${#POSTGRES_APP_DB}" -gt 58 ]; then
		echo "init: POSTGRES_APP_DB must match ${identifier} and have at most 58 characters." >&2
		return 1
	fi
	if [ -z "${POSTGRES_APP_PASSWORD:-}" ]; then
		echo "init: POSTGRES_APP_PASSWORD is empty." >&2
		return 1
	fi

	# La gemela se deriva igual que en tests/integration/conftest.py. Se pasa
	# solo en el entorno de este psql, sin exportarla al shell del entrypoint.
	APP_TEST_DB="${POSTGRES_APP_DB}_test" psql \
		--no-psqlrc \
		-v ON_ERROR_STOP=1 \
		--username "${POSTGRES_USER:-postgres}" \
		--dbname postgres <<-'SQL' || return 1
		-- Si una sentencia falla, que el log del servidor no guarde su texto
		-- (CREATE ROLE lleva la clave).
		SET log_min_error_statement = panic;
		SET log_statement = 'none';
		\getenv app_user POSTGRES_APP_USER
		\getenv app_password POSTGRES_APP_PASSWORD
		\getenv app_db POSTGRES_APP_DB
		\getenv app_test_db APP_TEST_DB
		CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password'
			NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
		CREATE DATABASE :"app_db" OWNER :"app_user";
		CREATE DATABASE :"app_test_db" OWNER :"app_user";
	SQL
}

create_app_role_and_databases
