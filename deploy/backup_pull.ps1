# Nightly pull-backup of reader-vm: the whole data/ dir (books, caches,
# jobs DB) -> D:\Backups\reader_vm, keeping the newest 14 archives.
# Scheduled as Windows task "ReaderVmBackup" (daily 20:00, or next boot).
#
# RESTORE (single file or all):
#   tar -tzf reader_vm_<stamp>.tar.gz                    # list contents
#   tar -xzf reader_vm_<stamp>.tar.gz data/site/...      # extract locally
#   scp -i ~\.ssh\gcp_reader <file> denis-reader@35.254.216.89:/tmp/
#   ssh: sudo mv /tmp/<file> /home/denis-reader/app/data/site/...
$ErrorActionPreference = "Stop"
$dest = "D:\Backups\reader_vm"
$key  = "$env:USERPROFILE\.ssh\gcp_reader"
$vm   = "denis-reader@35.254.216.89"

New-Item -ItemType Directory -Force $dest | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmm"
$file  = Join-Path $dest "reader_vm_$stamp.tar.gz"

ssh -i $key -o ConnectTimeout=20 $vm `
  "sudo tar czf /tmp/backup.tar.gz -C /home/denis-reader/app data && sudo chown denis-reader /tmp/backup.tar.gz"
scp -q -i $key "${vm}:/tmp/backup.tar.gz" $file
ssh -i $key $vm "rm -f /tmp/backup.tar.gz"

$size = (Get-Item $file).Length
if ($size -lt 10kb) { throw "backup suspiciously small: $size bytes" }

Get-ChildItem $dest -Filter "reader_vm_*.tar.gz" |
  Sort-Object Name -Descending | Select-Object -Skip 14 |
  Remove-Item -Force -Confirm:$false

Add-Content (Join-Path $dest "backup.log") `
  "$(Get-Date -Format s) OK $file $size bytes"
