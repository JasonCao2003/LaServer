# Repository Guidelines

## Project Structure & Module Organization

- `services/game-api/` contains the Python Flask server. `index.py` initializes the application; `business/account/`, `business/admin/`, and `business/service/` hold endpoint and service code. Shared behavior lives in `business/helpers/`, with database models in `infrastructure/database/models.py`.
- `services/game-api/resources/images/` and `services/game-api/resources/static_responses/` contain image assets and response fixtures. Treat database and runtime files as operational state.
- `services/realtime-server/src/` contains the Node.js ES-module service, organized into `api`, `config`, `domain`, `protocol`, `services`, `session`, `transport`, and `observability`. Protocol adaptation notes are in `services/realtime-server/docs/`.
- The admin client is in `apps/admin/admin_panel_client.py`; binary patch utilities are in `tools/patches/`. Edit active source files rather than `*.bak*`, `src.bak_*`, or archives under `bak/`.

## Build, Test, and Development Commands

Run UDP commands from `services/realtime-server/` with Node.js 20 or newer:

- `npm ci` installs dependencies from the lockfile.
- `npm run dev` starts the service with file watching.
- `npm start` runs the service normally.
- `npm run check` checks syntax in `src/main.js` only.
- `npm test` invokes Node's built-in test runner.

From the repository root, use `python -u services/game-api/sky_boot.py` to launch the Python server on Windows; this bootstrap handles a documented WMI startup issue. Use `python apps/admin/admin_panel_client.py` for the admin client. No Python dependency manifest is present; use a prepared environment.

## Coding Style & Naming Conventions

Follow surrounding code: four-space indentation and `snake_case` functions/modules in Python; two-space indentation, single quotes, semicolons, and explicit `.js` imports in JavaScript. JavaScript classes use `PascalCase`, while functions use `camelCase`. Keep protocol-version behavior in `src/protocol/versions/`. No shared formatter or lint configuration is supplied.

## Testing Guidelines

No dedicated automated test suite or coverage threshold is evident. Add focused UDP regression tests as `*.test.js` using `node:test`. For Python changes, validate affected endpoints with disposable data. For networking changes, verify connection, room join, and the affected packet flow with the relevant client version. Record commands and results.

## Commit & Pull Request Guidelines

This snapshot has no Git metadata, so existing commit conventions cannot be verified. Use concise imperative subjects, such as `Fix room join timeout`. PRs should describe the behavior change, affected client versions, validation, and any configuration or database impact. Link issues and attach screenshots for UI changes.

## Security & Configuration

Never include private keys, credentials, account databases, or raw player packet logs in commits or review attachments. Use disposable database copies for development. UDP settings are defined in `src/config/env.js`; choose isolated `ROOM_PORT` and `HTTP_PORT` values when running alongside existing services.


## Runtime Layout

- Flask instance data is in `services/game-api/var/data/instance/`; the separate legacy database is in `services/game-api/var/data/legacy/`. Do not merge them.
- Certificates and private keys are in `services/game-api/var/certs/`. Logs are in each service's `logs/` directory.
- Business imports use `business.helpers.<module>` and `infrastructure.database.models`; static response code is in `infrastructure.responses`, with data in `resources/static_responses/`.
