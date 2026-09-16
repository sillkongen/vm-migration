#!/usr/bin/env bash
#================================================================
# nag.sh
#
# Build/install a dpkg-trigger package which removes the
# Proxmox subscription dialog and reapplies the patch whenever
# proxmox-widget-toolkit is upgraded.
#
# Optionally converts Proxmox enterprise repositories to
# no-subscription repositories.
#
# Supported:
#   Proxmox VE 8 / Debian 12 Bookworm
#   Proxmox VE 9 / Debian 13 Trixie
#
# Usage:
#
#   sudo ./nag.sh
#       Install/update nag patch package
#
#   sudo ./nag.sh --community
#       Disable detected enterprise repositories and configure
#       matching no-subscription repositories
#
#   sudo ./nag.sh --check
#       Check environment and patch compatibility only
#
#   sudo ./nag.sh --uninstall
#       Remove patch package and restore vendor toolkit files
#
#================================================================

set -euo pipefail
IFS=$'\n\t'

readonly SELF="${0##*/}"
readonly PKGNAME="pve-nonag-trigger"
readonly PKG_VERSION="2.0.2"
readonly JS_DIR="/usr/share/javascript/proxmox-widget-toolkit"
readonly PATCH_MARKER="nag_screen_removed"

WORKDIR=""
PVE_MAJOR=""
CODENAME=""

#================================================================
# Logging
#================================================================

log() {
    printf '%s %s\n' "$1" "$2"
}

log_ok() {
    log '✓' "$1"
}

log_info() {
    log '-' "$1"
}

log_warn() {
    log '!' "$1"
}

log_err() {
    log '✗' "$1" >&2
}

die() {
    log_err "$*"
    exit 1
}

#================================================================
# Cleanup
#================================================================

cleanup() {
    if [[ -n "${WORKDIR:-}" && -d "$WORKDIR" ]]; then
        rm -rf -- "$WORKDIR"
    fi
}

trap cleanup EXIT

#================================================================
# Basic validation
#================================================================

need_root() {
    (( EUID == 0 )) ||
        die "Run $SELF as root."
}

need_command() {
    command -v "$1" >/dev/null 2>&1 ||
        die "Required command not found: $1"
}

check_dependencies() {
    local cmd

    for cmd in \
        apt-get \
        awk \
        cmp \
        cp \
        date \
        dpkg \
        dpkg-deb \
        dpkg-query \
        grep \
        install \
        mktemp \
        mv \
        pveversion \
        sed \
        systemctl \
        tar \
        wc
    do
        need_command "$cmd"
    done
}

#================================================================
# Detect Debian / Proxmox
#================================================================

detect_environment() {
    [[ -r /etc/os-release ]] ||
        die "/etc/os-release not found."

    CODENAME="$(
        awk -F= '
            $1 == "VERSION_CODENAME" {
                value = $2
                gsub(/^"/, "", value)
                gsub(/"$/, "", value)
                print value
                exit
            }
        ' /etc/os-release
    )"

    [[ -n "$CODENAME" ]] ||
        die "Unable to determine Debian codename."

    local pve_version

    pve_version="$(pveversion 2>/dev/null || true)"

    if [[ "$pve_version" =~ pve-manager/([0-9]+)\. ]]; then
        PVE_MAJOR="${BASH_REMATCH[1]}"
    else
        die "Unable to determine Proxmox VE major version from: $pve_version"
    fi

    case "$PVE_MAJOR:$CODENAME" in
        8:bookworm)
            ;;
        9:trixie)
            ;;
        *)
            die "Unsupported or unexpected combination: PVE $PVE_MAJOR / Debian $CODENAME"
            ;;
    esac

    log_ok "Detected Proxmox VE $PVE_MAJOR on Debian $CODENAME"
}

#================================================================
# Working directory
#================================================================

create_workdir() {
    [[ -z "$WORKDIR" ]] ||
        return 0

    WORKDIR="$(mktemp -d -t pve-nonag.XXXXXXXX)"

    [[ -d "$WORKDIR" ]] ||
        die "Unable to create temporary directory."

    chmod 700 "$WORKDIR"
}

#================================================================
# Build dpkg trigger package
#================================================================

build_pkg() {
    local pkgroot="$WORKDIR/$PKGNAME"
    local debdir="$pkgroot/DEBIAN"
    local prefix="$pkgroot/usr/local"
    local patcher="$prefix/bin/pve-nonag-patch.sh"

    log_info "Building $PKGNAME $PKG_VERSION ..."

    install -dm755 \
        "$debdir" \
        "$prefix/bin"

    cat > "$debdir/control" <<EOF
Package: $PKGNAME
Version: $PKG_VERSION
Section: admin
Priority: optional
Architecture: all
Depends: bash, proxmox-widget-toolkit (>= 1)
Maintainer: localhost <admin@localhost>
Description: Re-applies a local Proxmox subscription-dialog patch after toolkit upgrades.
EOF

    cat > "$debdir/triggers" <<EOF
interest-noawait $JS_DIR/proxmoxlib.js
interest-noawait $JS_DIR/proxmoxlib.min.js
EOF

    cat > "$debdir/postinst" <<'EOF'
#!/bin/sh
set -u

case "${1:-}" in
    configure|triggered)

        if ! /usr/local/bin/pve-nonag-patch.sh; then

            printf '%s\n' \
                "WARNING: pve-nonag-trigger could not safely patch the current proxmox-widget-toolkit." \
                >&2

            if command -v logger >/dev/null 2>&1; then
                logger \
                    -t pve-nonag-trigger \
                    "WARNING: current proxmox-widget-toolkit could not be safely patched" \
                    2>/dev/null || true
            fi
        fi

        ;;
esac

exit 0
EOF

    chmod 755 "$debdir/postinst"

    cat > "$patcher" <<'EOF'
#!/usr/bin/env bash

set -euo pipefail
IFS=$'\n\t'

readonly JS_DIR="/usr/share/javascript/proxmox-widget-toolkit"
readonly PATCH_MARKER="nag_screen_removed"

log() {
    printf '%s %s\n' "$1" "$2"
}

log_ok() {
    log '✓' "$1"
}

log_info() {
    log '-' "$1"
}

log_warn() {
    log '!' "$1"
}

log_err() {
    log '✗' "$1" >&2
}

already_patched() {
    local file="$1"

    grep -qF "//$PATCH_MARKER" "$file"
}

count_fixed_matches() {
    local pattern="$1"
    local file="$2"
    local output

    output="$(
        grep -oF -- "$pattern" "$file" 2>/dev/null ||
            true
    )"

    if [[ -z "$output" ]]; then
        printf '0\n'
    else
        printf '%s\n' "$output" |
            wc -l |
            tr -d '[:space:]'
    fi
}

count_regex_matches() {
    local pattern="$1"
    local file="$2"
    local output

    output="$(
        grep -oE -- "$pattern" "$file" 2>/dev/null ||
            true
    )"

    if [[ -z "$output" ]]; then
        printf '0\n'
    else
        printf '%s\n' "$output" |
            wc -l |
            tr -d '[:space:]'
    fi
}

replace_file_safely() {
    local original="$1"
    local temporary="$2"

    chmod --reference="$original" "$temporary"

    if command -v chown >/dev/null 2>&1; then
        chown --reference="$original" "$temporary"
    fi

    mv -f -- "$temporary" "$original"
}

patch_main_js() {
    local file="$1"

    [[ -f "$file" ]] || {
        log_info "Main JS file not present: $file"
        return 0
    }

    if already_patched "$file"; then
        log_ok "Main JS file already patched"
        return 0
    fi

    local expression="res.data.status.toLowerCase() !== 'active'"
    local count

    count="$(
        count_fixed_matches \
            "$expression" \
            "$file"
    )"

    #
    # PVE 9 can contain multiple legitimate copies of the exact
    # same subscription check.
    #
    # Allow a small bounded number only.
    #
    if (( count < 1 || count > 4 )); then
        log_warn \
            "Main JS subscription expression count is $count; refusing unsafe patch"
        return 1
    fi

    log_info \
        "Main JS: found $count supported subscription check(s)"

    local tmp

    tmp="$(mktemp "${file}.XXXXXX")"

    cp -a -- "$file" "$tmp"

    sed \
        "s/res\.data\.status\.toLowerCase() !== 'active'/false/g" \
        "$file" > "$tmp"

    if cmp -s "$file" "$tmp"; then
        rm -f -- "$tmp"

        log_err "Main JS patch produced no change"
        return 1
    fi

    local remaining

    remaining="$(
        count_fixed_matches \
            "$expression" \
            "$tmp"
    )"

    if (( remaining != 0 )); then
        rm -f -- "$tmp"

        log_err \
            "Main JS verification failed: $remaining subscription check(s) remain"

        return 1
    fi

    printf '\n//%s\n' "$PATCH_MARKER" >> "$tmp"

    replace_file_safely "$file" "$tmp"

    log_ok \
        "Main JS file patched ($count replacement(s))"

    return 0
}

patch_minified_js() {
    local file="$1"

    [[ -f "$file" ]] || {
        log_info "Minified JS file not present: $file"
        return 0
    }

    if already_patched "$file"; then
        log_ok "Minified JS file already patched"
        return 0
    fi

    local regex_not_equal
    local regex_equal

    regex_not_equal='"active"!==[A-Za-z_$][A-Za-z0-9_$]*\.data\.status\.toLowerCase\(\)'
    regex_equal='"active"===[A-Za-z_$][A-Za-z0-9_$]*\.data\.status\.toLowerCase\(\)'

    local count_not_equal
    local count_equal
    local total

    count_not_equal="$(
        count_regex_matches \
            "$regex_not_equal" \
            "$file"
    )"

    count_equal="$(
        count_regex_matches \
            "$regex_equal" \
            "$file"
    )"

    total=$(( count_not_equal + count_equal ))

    if (( total == 0 )); then
        log_warn \
            "No known subscription expression found in minified JS"
        return 1
    fi

    if (( total > 4 )); then
        log_warn \
            "Unexpected number of minified subscription expressions ($total); refusing unsafe patch"
        return 1
    fi

    log_info \
        "Minified JS: found $total supported subscription check(s)"

    local tmp

    tmp="$(mktemp "${file}.XXXXXX")"

    cp -a -- "$file" "$tmp"

    sed -E \
        -e 's/"active"!==[A-Za-z_$][A-Za-z0-9_$]*\.data\.status\.toLowerCase\(\)/false/g' \
        -e 's/"active"===[A-Za-z_$][A-Za-z0-9_$]*\.data\.status\.toLowerCase\(\)/true/g' \
        "$file" > "$tmp"

    if cmp -s "$file" "$tmp"; then
        rm -f -- "$tmp"

        log_err "Minified JS patch produced no change"
        return 1
    fi

    local remaining_not_equal
    local remaining_equal

    remaining_not_equal="$(
        count_regex_matches \
            "$regex_not_equal" \
            "$tmp"
    )"

    remaining_equal="$(
        count_regex_matches \
            "$regex_equal" \
            "$tmp"
    )"

    if (( remaining_not_equal + remaining_equal != 0 )); then
        rm -f -- "$tmp"

        log_err \
            "Minified JS verification failed: known subscription expressions remain"

        return 1
    fi

    printf '\n//%s\n' "$PATCH_MARKER" >> "$tmp"

    replace_file_safely "$file" "$tmp"

    log_ok \
        "Minified JS file patched ($total replacement(s))"

    return 0
}

main() {
    local failures=0

    patch_main_js \
        "$JS_DIR/proxmoxlib.js" ||
        failures=$(( failures + 1 ))

    patch_minified_js \
        "$JS_DIR/proxmoxlib.min.js" ||
        failures=$(( failures + 1 ))

    if (( failures != 0 )); then
        log_warn \
            "$failures toolkit file(s) could not be safely patched"

        return 1
    fi

    return 0
}

main "$@"
EOF

    chmod 755 "$patcher"

    local output_deb

    output_deb="$WORKDIR/${PKGNAME}_${PKG_VERSION}_all.deb"

    dpkg-deb \
        --root-owner-group \
        --build \
        "$pkgroot" \
        "$output_deb" \
        >/dev/null

    [[ -f "$output_deb" ]] ||
        die "dpkg-deb did not create $output_deb"

    log_ok "Package built: $output_deb"
}

#================================================================
# Install package
#================================================================

install_pkg() {
    local deb="$WORKDIR/${PKGNAME}_${PKG_VERSION}_all.deb"

    [[ -f "$deb" ]] ||
        die "Package was not built."

    log_info "Installing $PKGNAME ..."

    dpkg -i "$deb"

    log_ok "Package installed"

    if /usr/local/bin/pve-nonag-patch.sh; then
        log_ok "Current toolkit patch verified"
    else
        die \
            "Package installed, but the current toolkit could not be safely patched."
    fi

    if systemctl try-restart pveproxy.service; then
        log_ok "pveproxy.service refreshed"
    else
        log_warn \
            "Unable to restart pveproxy.service automatically"
    fi
}

#================================================================
# Check patch compatibility only
#================================================================

check_patch() {
    local failures=0
    local files_found=0
    local file

    log_info "Checking current toolkit files ..."

    for file in \
        "$JS_DIR/proxmoxlib.js" \
        "$JS_DIR/proxmoxlib.min.js"
    do

        if [[ ! -f "$file" ]]; then
            log_info "Not present: $file"
            continue
        fi

        files_found=$(( files_found + 1 ))

        if grep -qF "//$PATCH_MARKER" "$file"; then
            log_ok "$(basename "$file") already patched"
            continue
        fi

        case "$(basename "$file")" in

            proxmoxlib.js)

                local main_count

                main_count="$(
                    {
                        grep -oF \
                            "res.data.status.toLowerCase() !== 'active'" \
                            "$file" \
                            2>/dev/null ||
                            true
                    } |
                    wc -l |
                    tr -d '[:space:]'
                )"

                if (( main_count >= 1 && main_count <= 4 )); then
                    log_ok \
                        "proxmoxlib.js contains $main_count supported patch pattern(s)"
                else
                    log_warn \
                        "proxmoxlib.js pattern count is $main_count; patch would be refused"

                    failures=$(( failures + 1 ))
                fi

                ;;

            proxmoxlib.min.js)

                local min_count

                min_count="$(
                    {
                        grep -oE \
                            '"active"(!==|===)[A-Za-z_$][A-Za-z0-9_$]*\.data\.status\.toLowerCase\(\)' \
                            "$file" \
                            2>/dev/null ||
                            true
                    } |
                    wc -l |
                    tr -d '[:space:]'
                )"

                if (( min_count >= 1 && min_count <= 4 )); then
                    log_ok \
                        "proxmoxlib.min.js contains $min_count supported patch pattern(s)"
                else
                    log_warn \
                        "proxmoxlib.min.js pattern count is $min_count; patch would be refused"

                    failures=$(( failures + 1 ))
                fi

                ;;
        esac
    done

    if (( files_found == 0 )); then
        die \
            "No proxmox-widget-toolkit Javascript files were found under $JS_DIR"
    fi

    if (( failures != 0 )); then
        log_err \
            "$failures toolkit file(s) are not compatible with the current patch logic"

        return 1
    fi

    log_ok "Current toolkit is compatible with the patch logic"

    return 0
}

#================================================================
# APT repository backup
#================================================================

backup_repositories() {
    local timestamp
    local backup

    timestamp="$(date '+%Y%m%d-%H%M%S')"
    backup="/root/proxmox-repositories-${timestamp}.tar.gz"

    log_info "Backing up APT repository configuration ..."

    local sources=()

    if [[ -e /etc/apt/sources.list ]]; then
        sources+=("/etc/apt/sources.list")
    fi

    if [[ -d /etc/apt/sources.list.d ]]; then
        sources+=("/etc/apt/sources.list.d")
    fi

    (( ${#sources[@]} > 0 )) ||
        die "No APT repository configuration was found."

    tar \
        -czf "$backup" \
        "${sources[@]}"

    [[ -s "$backup" ]] ||
        die "Unable to create repository backup."

    log_ok "Repository backup: $backup"
}

#================================================================
# Disable enterprise repository in legacy .list file
#================================================================

disable_enterprise_list_file() {
    local file="$1"

    [[ -f "$file" ]] ||
        return 0

    if grep -Eq \
        '^[[:space:]]*deb .*enterprise\.proxmox\.com' \
        "$file"
    then

        sed -Ei \
            's|^([[:space:]]*deb .*enterprise\.proxmox\.com.*)$|# disabled by nag.sh: \1|' \
            "$file"

        log_ok "Disabled enterprise entries in $file"
    fi
}

#================================================================
# Disable deb822 enterprise repository
#================================================================

disable_enterprise_sources_file() {
    local file="$1"

    [[ -f "$file" ]] ||
        return 0

    grep -q \
        'enterprise.proxmox.com' \
        "$file" ||
        return 0

    if grep -q \
        'download.proxmox.com' \
        "$file"
    then
        die \
            "Refusing to modify mixed enterprise/community source file: $file"
    fi

    if grep -qE \
        '^Enabled:[[:space:]]*' \
        "$file"
    then

        sed -Ei \
            's/^Enabled:[[:space:]]*.*/Enabled: no/' \
            "$file"

    else

        printf '\nEnabled: no\n' >> "$file"
    fi

    log_ok "Disabled enterprise repository in $file"
}

#================================================================
# Detect existing PVE no-subscription repository
#================================================================

pve_nosub_exists() {
    grep -Rqs \
        --include='*.list' \
        --include='*.sources' \
        'pve-no-subscription' \
        /etc/apt/sources.list \
        /etc/apt/sources.list.d \
        2>/dev/null
}

#================================================================
# Configure PVE no-subscription repository
#================================================================

configure_pve_repository() {
    if pve_nosub_exists; then
        log_ok \
            "PVE no-subscription repository already configured"

        return 0
    fi

    case "$PVE_MAJOR" in

        9)

            cat > /etc/apt/sources.list.d/proxmox.sources <<EOF
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: $CODENAME
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF

            log_ok \
                "Configured PVE 9 no-subscription repository"

            ;;

        8)

            cat > /etc/apt/sources.list.d/pve-no-subscription.list <<EOF
deb http://download.proxmox.com/debian/pve $CODENAME pve-no-subscription
EOF

            log_ok \
                "Configured PVE 8 no-subscription repository"

            ;;

        *)

            die "Unsupported PVE major version: $PVE_MAJOR"
            ;;
    esac
}

#================================================================
# Detect Ceph release
#================================================================

detect_ceph_version_sources() {
    local file="$1"

    sed -nE \
        's|^[[:space:]]*URIs:[[:space:]]*https?://enterprise\.proxmox\.com/debian/ceph-([^[:space:]]+).*|\1|p' \
        "$file" |
        head -n1
}

detect_ceph_version_list() {
    local file="$1"

    sed -nE \
        's|^[[:space:]]*deb[[:space:]]+[^[:space:]]*enterprise\.proxmox\.com/debian/ceph-([^[:space:]]+).*|\1|p' \
        "$file" |
        head -n1
}

#================================================================
# Detect existing Ceph no-subscription repository
#================================================================

ceph_nosub_exists() {
    grep -RqsE \
        --include='*.list' \
        --include='*.sources' \
        'download\.proxmox\.com/debian/ceph-|Components:[[:space:]]*no-subscription' \
        /etc/apt/sources.list \
        /etc/apt/sources.list.d \
        2>/dev/null
}

#================================================================
# Ceph repository conversion
#================================================================

configure_ceph_repository() {
    if ceph_nosub_exists; then
        log_ok \
            "Ceph no-subscription repository already configured"

        return 0
    fi

    local ceph_sources="/etc/apt/sources.list.d/ceph.sources"

    if [[ -f "$ceph_sources" ]] &&
       grep -q \
           'enterprise.proxmox.com/debian/ceph-' \
           "$ceph_sources"
    then

        local ceph_version

        ceph_version="$(
            detect_ceph_version_sources \
                "$ceph_sources"
        )"

        [[ -n "$ceph_version" ]] ||
            die \
                "Enterprise Ceph repository detected, but Ceph release could not be determined."

        disable_enterprise_sources_file \
            "$ceph_sources"

        cat > /etc/apt/sources.list.d/ceph-no-subscription.sources <<EOF
Types: deb
URIs: http://download.proxmox.com/debian/ceph-$ceph_version
Suites: $CODENAME
Components: no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
EOF

        log_ok \
            "Configured Ceph $ceph_version no-subscription repository"

        return 0
    fi

    local ceph_list="/etc/apt/sources.list.d/ceph.list"

    if [[ -f "$ceph_list" ]] &&
       grep -Eq \
           '^[[:space:]]*deb .*enterprise\.proxmox\.com/debian/ceph-' \
           "$ceph_list"
    then

        local ceph_version

        ceph_version="$(
            detect_ceph_version_list \
                "$ceph_list"
        )"

        [[ -n "$ceph_version" ]] ||
            die \
                "Enterprise Ceph repository detected, but Ceph release could not be determined."

        disable_enterprise_list_file \
            "$ceph_list"

        cat > /etc/apt/sources.list.d/ceph-no-subscription.list <<EOF
deb http://download.proxmox.com/debian/ceph-$ceph_version $CODENAME no-subscription
EOF

        log_ok \
            "Configured Ceph $ceph_version no-subscription repository"

        return 0
    fi

    log_info \
        "No enterprise Ceph repository detected; Ceph configuration left unchanged"
}

#================================================================
# Convert repositories
#================================================================

convert_to_community() {
    log_info \
        "Converting detected Proxmox repositories to no-subscription ..."

    backup_repositories

    disable_enterprise_list_file \
        /etc/apt/sources.list.d/pve-enterprise.list

    disable_enterprise_sources_file \
        /etc/apt/sources.list.d/pve-enterprise.sources

    configure_pve_repository
    configure_ceph_repository

    log_info \
        "Validating repository configuration with apt-get update ..."

    if apt-get update; then
        log_ok "APT repositories validated successfully"
    else
        die \
            "apt-get update failed. Repository backup is available under /root."
    fi

    log_ok "Repository conversion complete"
}

#================================================================
# Uninstall
#================================================================

uninstall_pkg() {
    log_info "Removing $PKGNAME ..."

    if dpkg-query \
        -W \
        -f='${Status}' \
        "$PKGNAME" \
        2>/dev/null |
       grep -qF \
        'install ok installed'
    then

        apt-get purge \
            -y \
            "$PKGNAME"

    else

        log_info "$PKGNAME is not installed"
    fi

    log_info \
        "Restoring proxmox-widget-toolkit vendor files ..."

    apt-get install \
        --reinstall \
        -y \
        proxmox-widget-toolkit

    if systemctl try-restart pveproxy.service; then
        log_ok "pveproxy.service refreshed"
    else
        log_warn \
            "Unable to restart pveproxy.service automatically"
    fi

    log_ok \
        "Patch package removed and vendor toolkit restored"
}

#================================================================
# Usage
#================================================================

usage() {
    cat <<EOF
Usage:

  $SELF
      Install or update the nag patch package.

  $SELF --community
      Convert detected Proxmox enterprise repositories to
      no-subscription repositories, then install the patch.

  $SELF --check
      Check environment and patch compatibility without modifying
      anything.

  $SELF --uninstall
      Remove the patch package and restore proxmox-widget-toolkit.

EOF
}

#================================================================
# Main
#================================================================

main() {
    need_root
    check_dependencies
    detect_environment

    case "${1:-}" in

        --community)

            convert_to_community

            create_workdir
            build_pkg
            install_pkg

            ;;

        --check)

            if check_patch; then
                exit 0
            else
                exit 1
            fi

            ;;

        --uninstall)

            uninstall_pkg

            ;;

        --help|-h)

            usage

            ;;

        "")

            create_workdir
            build_pkg
            install_pkg

            ;;

        *)

            log_err "Unknown option: $1"
            usage >&2
            exit 2

            ;;
    esac
}

main "$@"
