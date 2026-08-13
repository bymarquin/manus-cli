_manus_completions() {
    local cur
    cur="${COMP_WORDS[COMP_CWORD]}"
    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=($(compgen -W "login use history status result --continue --file --project --timeout --connector --json" -- "$cur"))
    else
        COMPREPLY=($(compgen -W "--continue --file --project --timeout --connector --json" -- "$cur"))
    fi
}
complete -F _manus_completions manus
