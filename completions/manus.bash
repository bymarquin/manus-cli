_manus_completions() {
    local cur prev subcommands flags
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD - 1]}"
    subcommands="login use history open alias connector confirm doctor status result"
    flags="--continue --file --project --timeout --connector --json --task --allow-secret --dry-run --no-gitignore"

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
        use)
            COMPREPLY=($(compgen -W "--as" -- "$cur"))
            ;;
        confirm)
            COMPREPLY=($(compgen -W "--input --task" -- "$cur"))
            ;;
        *)
            COMPREPLY=($(compgen -W "$flags" -- "$cur"))
            ;;
    esac
}
complete -F _manus_completions manus
