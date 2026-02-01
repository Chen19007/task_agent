#!/usr/bin/env bash

get_hello_user() {
  local user_name
  user_name="${USER:-$(whoami)}"
  echo "hello ${user_name}"
}

print_context() {
  printf 'AGENT_START_DIR=%s\n' "${AGENT_START_DIR:-}"
  printf 'AGENT_PROJECT_DIR=%s\n' "${AGENT_PROJECT_DIR:-}"
  printf 'HINT_MODULE_DIR=%s\n' "${HINT_MODULE_DIR:-}"
}
