# Proxmox VM Migration Tool

A comprehensive Python script for migrating Virtual Machines between Proxmox hosts that are not in a cluster. This tool supports both local and remote migrations with various options for backup, transfer, and restoration.

## Features

- **Non-clustered Migration**: Migrate VMs between standalone Proxmox hosts
- **Flexible Authentication**: Support for both password and API token authentication
- **Backup and Restore**: Create backups before migration for safety
- **Multiple Storage Options**: Support for different storage backends with automatic fallback
- **SSH Integration**: Secure file transfer capabilities
- **Comprehensive Logging**: Detailed logging for troubleshooting
- **CLI Interface**: Easy-to-use command-line interface
- **Progress Tracking**: Real-time progress monitoring for long-running operations
- **Storage Fallback**: Automatic fallback from local-lvm → local-zfs → local

## Prerequisites

- Python 3.6 or higher
- Access to source and target Proxmox hosts
- Network connectivity between hosts
- Appropriate permissions on both hosts
- SSH access to both hosts for backup operations

## Installation

1. Clone or download this repository:
```bash
git clone <repository-url>
cd vm-migration
```

2. Install required dependencies:
```bash
pip install -r requirements.txt
```

3. Initialize configuration:
```bash
python proxmox_migrator.py init-config
```

4. Edit the configuration file `proxmox_config.json` with your actual API tokens.
5. Follow the token setup guide in `create_token_guide.md` for detailed instructions.

## Configuration

The script uses a JSON configuration file for storing connection details. You can create it manually or use the `init-config` command.

### Configuration File Structure

```json
{
  "source": {
    "hostname": "192.168.1.212",
    "port": 8006,
    "username": "root@pam",
    "password": "your_password_here",
    "verify_ssl": false
  },
  "target": {
    "hostname": "192.168.1.232",
    "port": 8006,
    "username": "root@pam",
    "password": "your_password_here",
    "verify_ssl": false
  },
  "ssh": {
    "username": "root",
    "password": null,
    "key_filename": null,
    "port": 22
  }
}
```

### Authentication Options

#### API Token Authentication (Recommended)
```json
{
  "username": "root@pam",
  "token_name": "vm-migration-tool",
  "token_value": "your_token_secret_here"
}
```

#### Password Authentication (Legacy)
```json
{
  "username": "root@pam",
  "password": "your_password"
}
```

**Note**: API tokens are strongly recommended for security. See `create_token_guide.md` for detailed setup instructions.

## Usage Examples

### Complete Migration Workflow

Here's a complete example of migrating VM 106 from host 192.168.1.212 to 192.168.1.232:

#### 1. List VMs on Source Host
```bash
python proxmox_migrator.py list-vms --host 192.168.1.212 --node prox-01 --config-file proxmox_config.json
```

**Output:**
```
VMs on 192.168.1.212 (prox-01):
----------------------------------------------------------------------------------------------------
VMID    Name                    Status      CPU     Memory      Disk Size   Storage
----------------------------------------------------------------------------------------------------
106     Ubuntu Server 22.04     running     1.0     2048 MB     20 GB       local
```

#### 2. Create Backup on Source Host
```bash
python proxmox_migrator.py backup --host 192.168.1.212 --node prox-01 --vm-id 106 --config-file proxmox_config.json
```

**Output:**
```
2025-07-19 18:37:30,877 - INFO - Creating backup for VM 106
Enter SSH password for root@192.168.1.212: 
🔄 Running backup command: vzdump 106 --storage local --compress gzip --mode snapshot
✅ Backup created successfully: /var/lib/vz/dump/vzdump-qemu-106-2025_07_19-18_49_32.vma.gz
```

#### 3. List Available Backups
```bash
python proxmox_migrator.py list-backups --host 192.168.1.212 --node prox-01 --config-file proxmox_config.json
```

**Output:**
```
Backups on 192.168.1.212 (prox-01):
----------------------------------------------------------------------------------------------------
Filename                                           Size         Date                 VMID
----------------------------------------------------------------------------------------------------
/var/lib/vz/dump/vzdump-qemu-106-2025_07_19-18_49_32.vma.gz 4404800637   Jul 19 18:55         106
```

#### 4. Transfer Backup to Target Host
```bash
python proxmox_migrator.py transfer-backup \
  --source-host 192.168.1.212 \
  --target-host 192.168.1.232 \
  --backup-file "/var/lib/vz/dump/vzdump-qemu-106-2025_07_19-18_49_32.vma.gz" \
  --source-node prox-01 \
  --target-node pve \
  --config-file proxmox_config.json
```

**Output:**
```
🔄 Transferring vzdump-qemu-106-2025_07_19-18_49_32.vma.gz from 192.168.1.212 to 192.168.1.232...
✅ File transferred successfully to /var/lib/vz/dump/vzdump-qemu-106-2025_07_19-18_49_32.vma.gz
✅ Backup transferred successfully to: /var/lib/vz/dump/vzdump-qemu-106-2025_07_19-18_49_32.vma.gz
```

#### 5. Import Backup on Target Host
```bash
python proxmox_migrator.py import-backup \
  --host 192.168.1.232 \
  --backup-file "/var/lib/vz/dump/vzdump-qemu-106-2025_07_19-18_49_32.vma.gz" \
  --vm-id 807 \
  --node pve \
  --storage local \
  --config-file proxmox_config.json
```

**Output:**
```
🔄 Trying to import to storage: local-lvm
 Command: qmrestore /var/lib/vz/dump/vzdump-qemu-106-2025_07_19-18_49_32.vma.gz 807 --storage local-lvm
============================================================
❌ Import to local-lvm failed with exit code 255
⚠️  Error: storage 'local-lvm' does not exist
🔄 Storage local-lvm not suitable, trying next storage...
🧹 Cleaning up any partial VM 807 before trying next storage...

🔄 Trying to import to storage: local-zfs
 Command: qmrestore /var/lib/vz/dump/vzdump-qemu-106-2025_07_19-18_49_32.vma.gz 807 --storage local-zfs
============================================================
✅ VM 807 imported successfully to storage local-zfs
```

### Other Useful Commands

#### List Nodes
```bash
python proxmox_migrator.py list-nodes --host 192.168.1.212 --config-file proxmox_config.json
```

#### List Tasks
```bash
python proxmox_migrator.py list-tasks --host 192.168.1.212 --node prox-01 --config-file proxmox_config.json
```

#### Monitor Specific Task
```bash
python proxmox_migrator.py monitor-task --host 192.168.1.212 --node prox-01 --task-id UPID:prox-01:00001234:12345678:ABC12345:qmrestore:106:root@pam: --config-file proxmox_config.json
```

#### Test Configuration
```bash
python proxmox_migrator.py test-config --config-file proxmox_config.json
```

## Migration Process

The migration process follows these steps:

1. **Status Check**: Verify the current status of the VM
2. **Graceful Shutdown**: Stop the VM if it's running
3. **Backup Creation**: Create a backup of the VM using `vzdump`
4. **File Transfer**: Transfer the backup to the target host using SCP/rsync
5. **VM Restoration**: Restore the VM on the target host using `qmrestore`
6. **Cleanup**: Remove temporary backup files

## Storage Considerations

### Storage Fallback Strategy

The tool implements an intelligent storage fallback strategy:

1. **local-lvm** (preferred for VM images)
2. **local-zfs** (fallback if local-lvm not available)
3. **local** (last resort)

The import process automatically tries each storage in order and cleans up any partial VM creation before trying the next storage.

### Local Storage
- Backups are stored in `/var/lib/vz/dump/` by default
- Ensure sufficient disk space for backups
- Consider using dedicated backup storage

### Network Storage
- NFS, CIFS, or other network storage can be used
- Ensure both hosts have access to shared storage
- Configure appropriate permissions

### Transfer Methods

The script supports multiple transfer methods:

1. **SCP/SFTP**: Direct file transfer between hosts
2. **rsync**: Efficient file transfer with resume capability
3. **Shared Storage**: Using NFS, CIFS, or other shared storage
4. **Manual Transfer**: Manual file transfer for air-gapped environments

## Security Considerations

- **Use API tokens** instead of passwords (strongly recommended)
- Enable SSL verification in production environments
- Use SSH keys for file transfers
- Restrict access to backup files
- Consider network segmentation for migration traffic
- Store tokens securely and never commit them to version control
- Use minimal required permissions for API tokens
- SSH passwords are prompted securely (no echo)

## Troubleshooting

### Common Issues

1. **Authentication Failed**
   - Verify username and password/token
   - Check if the user has appropriate permissions
   - Ensure the API is accessible

2. **VM Won't Stop**
   - Check if the VM has running processes
   - Verify guest agent is installed and running
   - Consider force stopping if necessary

3. **Insufficient Storage**
   - Check available disk space on both hosts
   - Clean up old backups
   - Consider using external storage

4. **Network Issues**
   - Verify network connectivity between hosts
   - Check firewall rules
   - Ensure ports are open (8006 for API, 22 for SSH)

5. **Storage Content Type Error**
   - The tool automatically handles storage fallback
   - Check available storages with `list-storages` command
   - Ensure target storage supports VM images

6. **VM Already Exists**
   - Use a different VM ID for the target
   - The tool will automatically clean up partial VM creation

### Logging

The script creates detailed logs in `vm_migration.log`. Check this file for detailed error information.

## Advanced Usage

### Custom Storage Configuration

```bash
python proxmox_migrator.py migrate \
  --source-host 192.168.1.212 \
  --target-host 192.168.1.232 \
  --vm-id 106 \
  --storage local-zfs \
  --backup-storage local
```

### Batch Migration

You can create a simple script for batch migrations:

```bash
#!/bin/bash
VMS=(100 101 102 103)

for vm_id in "${VMS[@]}"; do
    echo "Migrating VM $vm_id..."
    python proxmox_migrator.py migrate \
      --source-host 192.168.1.212 \
      --target-host 192.168.1.232 \
      --vm-id $vm_id \
      --config-file proxmox_config.json
done
```

### PowerShell Script (Windows)

```powershell
$VMs = @(100, 101, 102, 103)

foreach ($vm_id in $VMs) {
    Write-Host "Migrating VM $vm_id..."
    python proxmox_migrator.py migrate `
      --source-host 192.168.1.212 `
      --target-host 192.168.1.232 `
      --vm-id $vm_id `
      --config-file proxmox_config.json
}
```

## Future Enhancements

- ESXi to Proxmox migration support
- Live migration capabilities
- GUI interface
- Web-based management
- Integration with monitoring systems
- Support for more storage backends
- Real-time progress bars for long operations
- Backup compression options
- Incremental backup support

## Contributing

Contributions are welcome! Please feel free to submit pull requests or open issues for bugs and feature requests.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For support and questions:
- Open an issue on GitHub
- Check the troubleshooting section
- Review the logs for detailed error information 