#!/usr/bin/env bash
set -euo pipefail

TEST_CGROUP="/sys/fs/cgroup/residual-executor-test"
DEMOTE_SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/scripts/kv-demote-zram.sh"
ALLOCATOR="$(mktemp /tmp/omarchy-anon-alloc.XXXXXX)"
SOURCE="${ALLOCATOR}.c"
PID=""

cleanup() {
  if [[ -n "$PID" ]]; then kill "$PID" 2>/dev/null || true; fi
  sudo rmdir "$TEST_CGROUP" 2>/dev/null || true
  rm -f "$ALLOCATOR" "$SOURCE"
}
trap cleanup EXIT

cat > "$SOURCE" <<'EOF'
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>
int main(void) {
    size_t size = 1024ULL * 1024ULL * 1024ULL;
    char *p = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) return 1;
    for (size_t i = 0; i < size; i += 4096) p[i] = (char)(i & 0xff);
    sleep(30);
    return 0;
}
EOF
gcc -O2 -Wall -Wextra -o "$ALLOCATOR" "$SOURCE"

sudo mkdir -p "$TEST_CGROUP"
echo 1 | sudo tee "$TEST_CGROUP/memory.oom.group" >/dev/null

"$ALLOCATOR" &
PID=$!
echo "$PID" | sudo tee "$TEST_CGROUP/cgroup.procs" >/dev/null
sleep 1
sudo "$DEMOTE_SCRIPT" "$TEST_CGROUP" $((512 * 1024 * 1024))
