# Sourced by the parity workflow right before either side starts its agents, so that
# both sides start from the same environment.
#
# Removes every variable from the runner's environment that Claude Code or Harbor's
# claude-code agent would pick up (the equivalence check runs with the same clean
# environment), then sets the credential under the configured name (AUTH_VAR) and the
# custom endpoint, if there is one. With DRY_RUN=true a dummy key is used instead.
# Expects AUTH_VAR and DRY_RUN (from config.env) and TB_API_KEY, TB_BASE_URL.

removed=""
for name in $(compgen -e); do
  case "$name" in
    ANTHROPIC_* | CLAUDE_* | AWS_* | MAX_THINKING_TOKENS | DISABLE_PROMPT_CACHING)
      unset "$name"
      removed="$removed $name"
      ;;
  esac
done
echo "agent_env.sh: removed from the environment:${removed:- nothing}"

case "$AUTH_VAR" in
  ANTHROPIC_API_KEY | ANTHROPIC_AUTH_TOKEN) ;;
  *) echo "::error::unexpected AUTH_VAR" >&2; exit 1 ;;
esac

if [ "$DRY_RUN" = true ]; then
  export "$AUTH_VAR=sk-ant-parity-dry-run-not-a-real-key"
else
  if [ -z "${TB_API_KEY:-}" ]; then echo "::error::the TB_API_KEY secret is empty" >&2; exit 1; fi
  export "$AUTH_VAR=$TB_API_KEY"
  if [ -n "${TB_BASE_URL:-}" ]; then
    # Claude Code appends /v1/messages itself, so the endpoint is given without /v1.
    base_url="${TB_BASE_URL%/}"
    base_url="${base_url%/v1}"
    if [ "$base_url" != "$TB_BASE_URL" ]; then
      echo "::notice::the endpoint's trailing / or /v1 was removed (Claude Code adds /v1 itself)"
    fi
    export ANTHROPIC_BASE_URL="$base_url"
  fi
fi
unset TB_API_KEY TB_BASE_URL
