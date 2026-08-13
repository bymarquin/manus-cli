#compdef manus

_manus() {
    local -a subcommands flags
    subcommands=(login use history open alias connector confirm doctor status result stop delete update project)
    flags=(--continue --file --project --timeout --connector --json --task --allow-secret --dry-run --no-gitignore --in-project --agent-profile)

    if (( CURRENT == 2 )); then
        compadd -a subcommands
        compadd -a flags
        return
    fi

    case "${words[2]}" in
        alias|connector)
            compadd list
            ;;
        project)
            compadd create list
            ;;
        use)
            compadd --as
            ;;
        confirm)
            compadd --input --task
            ;;
        delete)
            compadd --yes
            ;;
        update)
            compadd --title --share --hide --show
            ;;
        *)
            compadd -a flags
            ;;
    esac
}

_manus
