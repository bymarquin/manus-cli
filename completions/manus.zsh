#compdef manus

_manus() {
    local -a subcommands flags
    subcommands=(login use history status result)
    flags=(--continue --file --project --timeout --connector --json)
    if (( CURRENT == 2 )); then
        compadd -a subcommands
        compadd -a flags
    else
        compadd -a flags
    fi
}

_manus
