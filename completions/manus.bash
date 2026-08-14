_manus_completions() {
    local cur prev subcommands flags
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD - 1]}"
    subcommands="login use history open alias connector confirm doctor status result stop delete update project code"
    flags="--continue --file --project --timeout --connector --json --task --allow-secret --dry-run --no-gitignore --in-project --agent-profile"

    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=($(compgen -W "$subcommands $flags" -- "$cur"))
        return
    fi

    case "${COMP_WORDS[1]}" in
        alias)
            COMPREPLY=($(compgen -W "list" -- "$cur"))
            ;;
        connector)
            COMPREPLY=($(compgen -W "list" -- "$cur"))
            ;;
        project)
            COMPREPLY=($(compgen -W "create list" -- "$cur"))
            ;;
        use)
            COMPREPLY=($(compgen -W "--as" -- "$cur"))
            ;;
        confirm)
            COMPREPLY=($(compgen -W "--input --task" -- "$cur"))
            ;;
        delete)
            COMPREPLY=($(compgen -W "--yes" -- "$cur"))
            ;;
        update)
            COMPREPLY=($(compgen -W "--title --share --hide --show" -- "$cur"))
            ;;
        code)
            COMPREPLY=($(compgen -W "--root --max-steps --command-timeout --timeout --approval --yes --json --agent-profile" -- "$cur"))
            ;;
        *)
            COMPREPLY=($(compgen -W "$flags" -- "$cur"))
            ;;
    esac
}
complete -F _manus_completions manus
