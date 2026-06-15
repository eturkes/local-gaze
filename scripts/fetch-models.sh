#!/bin/sh
# Fetch + verify local-gaze CV models. HOST-ONLY (needs network + openvino for the
# MediaPipe TFLite->IR conversion). POSIX sh.
#
# Trust-on-first-use: MANIFEST ships sha256=TODO per artifact. On first download we
# compute the digest and pin it back into MANIFEST; on later runs we verify against
# the pinned value and abort on mismatch. Downloaded archives are scanned and
# rejected if they contain executable payloads.
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)
MODELS_DIR="$REPO_DIR/models"
MANIFEST="$MODELS_DIR/MANIFEST"

[ -f "$MANIFEST" ] || { echo "FATAL: no MANIFEST at $MANIFEST" >&2; exit 1; }

GAZE_DIR="$MODELS_DIR/gaze"
HAND_DIR="$MODELS_DIR/hand"
mkdir -p "$GAZE_DIR" "$HAND_DIR"

log() { printf '[fetch-models] %s\n' "$*"; }
die() { printf '[fetch-models] FATAL: %s\n' "$*" >&2; exit 1; }

# Prefer curl, fall back to wget.
download() {  # download <url> <dest>
    _url=$1; _dest=$2
    if command -v curl >/dev/null 2>&1; then
        curl -fSL --proto '=https' --tlsv1.2 -o "$_dest" "$_url"
    elif command -v wget >/dev/null 2>&1; then
        wget --https-only -O "$_dest" "$_url"
    else
        die "need curl or wget"
    fi
}

sha256_of() {  # sha256_of <file> -> hex digest on stdout
    sha256sum "$1" | awk '{print $1}'
}

# Look up a TAB-separated MANIFEST field by artifact name. col: 2=url 3=sha 5=rev.
manifest_field() {  # manifest_field <name> <col>
    awk -F '\t' -v n="$1" -v c="$2" \
        '!/^#/ && $1==n { print $c; found=1 } END { if (!found) exit 3 }' "$MANIFEST"
}

# Pin a freshly-computed digest back into MANIFEST (replaces TODO for that artifact).
pin_sha() {  # pin_sha <name> <digest>
    _name=$1; _dig=$2; _tmp="$MANIFEST.tmp"
    awk -F '\t' -v OFS='\t' -v n="$_name" -v d="$_dig" \
        '!/^#/ && $1==n { $3=d } { print }' "$MANIFEST" > "$_tmp"
    mv "$_tmp" "$MANIFEST"
}

# Download an artifact to dest, verifying (or pinning) its sha256 from MANIFEST.
fetch_verified() {  # fetch_verified <name> <dest>
    _name=$1; _dest=$2
    _url=$(manifest_field "$_name" 2) || die "no MANIFEST entry for $_name"
    _want=$(manifest_field "$_name" 3)
    log "downloading $_name"
    download "$_url" "$_dest"
    _got=$(sha256_of "$_dest")
    if [ "$_want" = "TODO" ]; then
        log "pinning sha256 for $_name = $_got"
        pin_sha "$_name" "$_got"
    elif [ "$_want" != "$_got" ]; then
        rm -f "$_dest"
        die "sha256 mismatch for $_name: want $_want got $_got"
    else
        log "verified $_name"
    fi
}

# Refuse archives whose entries look executable (exec perm bit or risky extension).
reject_executable_archive() {  # reject_executable_archive <zipfile>
    _zip=$1
    command -v unzip >/dev/null 2>&1 || die "need unzip"
    # -Z -l gives a long listing; col 1 is the unix permission string. Any execute
    # bit (owner/group/other) on a file entry is rejected.
    if unzip -Z -l "$_zip" 2>/dev/null \
        | awk 'NR>2 && $1 ~ /^-/ && $1 ~ /x/ { found=1 } END { exit found }'; then
        :
    else
        die "archive $_zip contains an executable entry; refusing"
    fi
    if unzip -Z1 "$_zip" 2>/dev/null \
        | grep -Eiq '\.(sh|bash|py|pl|rb|exe|bin|so|dylib|dll|elf|run|com|bat|cmd)$'; then
        die "archive $_zip contains an executable-looking file; refusing"
    fi
}

# ---- gaze: 8 OMZ FP16 files ------------------------------------------------
log "=== gaze (Open Model Zoo, FP16) ==="
for base in \
    face-detection-retail-0004 \
    head-pose-estimation-adas-0001 \
    facial-landmarks-35-adas-0002 \
    gaze-estimation-adas-0002
do
    fetch_verified "$base.xml" "$GAZE_DIR/$base.xml"
    fetch_verified "$base.bin" "$GAZE_DIR/$base.bin"
done

# ---- hand: MediaPipe bundle -> extract tflite -> convert to IR --------------
log "=== hand (MediaPipe -> OpenVINO IR) ==="
TASK="$HAND_DIR/hand_landmarker.task"
fetch_verified "hand_landmarker.task" "$TASK"
reject_executable_archive "$TASK"

EXTRACT="$HAND_DIR/extract"
rm -rf "$EXTRACT"
mkdir -p "$EXTRACT"
log "unzipping palm_detection_full.tflite + hand_landmark_full.tflite"
unzip -o -j "$TASK" \
    'palm_detection_full.tflite' 'hand_landmark_full.tflite' -d "$EXTRACT" \
    || die "expected tflite members missing from $TASK"

PALM_TFLITE="$EXTRACT/palm_detection_full.tflite"
LM_TFLITE="$EXTRACT/hand_landmark_full.tflite"
[ -f "$PALM_TFLITE" ] || die "palm_detection_full.tflite not extracted"
[ -f "$LM_TFLITE" ] || die "hand_landmark_full.tflite not extracted"

convert_tflite() {  # convert_tflite <src.tflite> <out_basename_without_ext>
    _src=$1; _out=$2
    log "converting $(basename "$_src") -> $(basename "$_out").xml"
    python3 -c "import openvino as ov; m=ov.convert_model('$_src'); ov.save_model(m, '$_out.xml')" \
        || die "openvino convert_model failed for $_src"
}

convert_tflite "$PALM_TFLITE" "$HAND_DIR/palm"
convert_tflite "$LM_TFLITE" "$HAND_DIR/landmark"
rm -rf "$EXTRACT"

log "done. gaze IR in $GAZE_DIR, hand IR in $HAND_DIR"
log "if any sha256 was TODO it is now pinned in $MANIFEST — commit MANIFEST."
