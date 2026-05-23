#!/usr/bin/env bash
set -euo pipefail

# Launch the HomePlug development VM with the same guest-visible NIC shape as
# the later wired Home Assistant setup. On Wi-Fi, macOS adds MACNAT on the host
# side; the companion relay handles only that host-side limitation.

WIFI_IF="${WIFI_IF:-en1}"
VM_MAC="${VM_MAC:-52:54:B0:16:85:DA}"
VM_NAME="${VM_NAME:-homeplug-direct-wifi}"
VM_UUID="${VM_UUID:-3E53D64C-2B8D-49C7-A78D-79B0CC0A2E91}"
VM_DIR="${VM_DIR:-/Users/moritz/Virtual Machines/HomeplugDev}"
DISK_IMAGE="${DISK_IMAGE:-$VM_DIR/homeplug-vz.raw}"
EFI_VARS="${EFI_VARS:-$VM_DIR/efi_vars.qemu-direct.fd}"
QEMU="${QEMU:-/opt/homebrew/bin/qemu-system-aarch64}"
QEMU_SHARE="${QEMU_SHARE:-/opt/homebrew/share/qemu}"
MEMORY="${MEMORY:-4096}"
CPUS="${CPUS:-4}"

exec sudo "$QEMU" \
  -L "$QEMU_SHARE" \
  -display none -serial null -monitor none \
  -nodefaults -vga none \
  -device "virtio-net-pci,mac=$VM_MAC,netdev=net0" \
  -netdev "vmnet-bridged,id=net0,ifname=$WIFI_IF" \
  -cpu host \
  -smp "cpus=$CPUS,sockets=1,cores=$CPUS,threads=1" \
  -machine virt \
  -accel hvf \
  -drive "if=pflash,format=raw,unit=0,file=$QEMU_SHARE/edk2-aarch64-code.fd,readonly=on" \
  -drive "if=pflash,format=raw,unit=1,file=$EFI_VARS" \
  -m "$MEMORY" \
  -device qemu-xhci,id=usb-controller-0 \
  -device virtio-blk-pci,drive=drive0,bootindex=0 \
  -drive "if=none,media=disk,id=drive0,file=$DISK_IMAGE,format=raw,discard=unmap,detect-zeroes=unmap" \
  -device virtio-rng-pci \
  -name "$VM_NAME" \
  -uuid "$VM_UUID"
