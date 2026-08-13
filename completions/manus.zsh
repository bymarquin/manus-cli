#compdef manus

_manus() {
    local -a subcommands flags
    subcommands=(login use history open alias connector confirm doctor status result)
    flags=(--continue --file --project --timeout --connector --json --task --allow-secret --dry-run --no-gitignore)

    if (( CURRENT == 2 )); then
        compadd -a subcommands
        compadd -a flags
        return
    fi

    case "${words[2]}" in
        alias|connector)
            compadd list
            ;;
        use)
            compadd --as
            ;;
        confirm)
            compadd --input --task
            ;;
        *)
            compadd -a flags
            ;;
    esac
}

_manus
