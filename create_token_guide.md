# Proxmox API Token Setup Guide

This guide will help you create and configure API tokens for secure authentication with the Proxmox VM Migration Tool.

## Why Use API Tokens?

- **More Secure**: No passwords stored in configuration files
- **Granular Permissions**: Fine-grained access control
- **Audit Trail**: Better logging and monitoring
- **No Password Changes**: Tokens don't expire when passwords change

## Step 1: Create API Token in Proxmox Web Interface

### 1.1 Access the Web Interface
1. Open your browser and navigate to `https://your-proxmox-host:8006`
2. Log in with your root credentials

### 1.2 Navigate to API Tokens
1. In the left sidebar, click on **Datacenter**
2. Click on **Permissions**
3. Click on **API Tokens**

### 1.3 Create New Token
1. Click the **Add** button
2. Fill in the token details:
   - **User**: Select the user (e.g., `root@pam`)
   - **Token ID**: Give it a descriptive name (e.g., `vm-migration-tool`)
   - **Comment**: Optional description (e.g., `Token for VM migration automation`)
   - **Privilege Separation**: Leave unchecked for full access, or check for limited privileges

### 1.4 Set Permissions
For VM migration, the token needs these permissions:

**Required Permissions:**
- `Datacenter.Allocate` - Create new VMs
- `Datacenter.Audit` - Read datacenter information
- `VM.Allocate` - Create and manage VMs
- `VM.Audit` - Read VM information
- `VM.Backup` - Create VM backups
- `VM.Clone` - Clone VMs
- `VM.Config.CDROM` - Configure VM CD-ROM
- `VM.Config.CPU` - Configure VM CPU
- `VM.Config.Cloudinit` - Configure cloud-init
- `VM.Config.Disk` - Configure VM disks
- `VM.Config.Memory` - Configure VM memory
- `VM.Config.Network` - Configure VM network
- `VM.Config.Options` - Configure VM options
- `VM.Monitor` - Monitor VM status
- `VM.PowerMgmt` - Start/stop VMs
- `VM.Snapshot` - Create/manage snapshots
- `Storage.Allocate` - Allocate storage space
- `Storage.Audit` - Read storage information

**Optional Permissions:**
- `VM.Config.CDROM` - If you need CD-ROM configuration
- `VM.Config.Cloudinit` - If using cloud-init
- `VM.Snapshot` - If you want snapshot capabilities

### 1.5 Generate Token
1. Click **Add** to create the token
2. **IMPORTANT**: Copy both the **Token ID** and **Secret** immediately
3. The secret will only be shown once!

## Step 2: Configure the Migration Tool

### 2.1 Update Configuration File
Edit your `proxmox_config.json` file:

```json
{
  "source": {
    "hostname": "source-proxmox.example.com",
    "port": 8006,
    "username": "root@pam",
    "token_name": "vm-migration-tool",
    "token_value": "your-actual-token-secret-here",
    "verify_ssl": false
  },
  "target": {
    "hostname": "target-proxmox.example.com",
    "port": 8006,
    "username": "root@pam",
    "token_name": "vm-migration-tool",
    "token_value": "your-actual-token-secret-here",
    "verify_ssl": false
  }
}
```

### 2.2 Test the Token
Test your token configuration:

```bash
# Test source host
python proxmox_migrator.py list-vms --host source-proxmox.example.com --config-file proxmox_config.json

# Test target host
python proxmox_migrator.py list-vms --host target-proxmox.example.com --config-file proxmox_config.json
```

## Step 3: SSH Key Setup (Optional but Recommended)

For secure file transfers, set up SSH key authentication:

### 3.1 Generate SSH Key (if you don't have one)
```bash
ssh-keygen -t rsa -b 4096 -C "vm-migration-tool"
```

### 3.2 Copy SSH Key to Proxmox Hosts
```bash
# Copy to source host
ssh-copy-id root@source-proxmox.example.com

# Copy to target host
ssh-copy-id root@target-proxmox.example.com
```

### 3.3 Update Configuration
Update your `proxmox_config.json` to include SSH settings:

```json
{
  "source": { ... },
  "target": { ... },
  "ssh": {
    "username": "root",
    "key_filename": "~/.ssh/id_rsa",
    "port": 22
  }
}
```

## Security Best Practices

### 1. Token Security
- Store tokens securely (not in version control)
- Use different tokens for different environments
- Rotate tokens regularly
- Use minimal required permissions

### 2. Network Security
- Use HTTPS for API connections
- Enable SSL verification in production
- Use SSH keys instead of passwords
- Restrict network access to Proxmox hosts

### 3. File Permissions
```bash
# Secure your configuration file
chmod 600 proxmox_config.json

# Secure SSH keys
chmod 600 ~/.ssh/id_rsa
chmod 644 ~/.ssh/id_rsa.pub
```

## Troubleshooting

### Common Issues

1. **"Authentication failed"**
   - Verify token name and secret are correct
   - Check if token has required permissions
   - Ensure username format is correct (e.g., `root@pam`)

2. **"Permission denied"**
   - Verify token has all required permissions
   - Check if user has access to the specific resources

3. **"SSL certificate verify failed"**
   - Set `"verify_ssl": false` for testing
   - Install proper SSL certificates for production

### Testing Commands

```bash
# Test API connection
curl -k -H "Authorization: PVEAPIToken=root@pam!token-name=token-secret" \
  https://your-proxmox-host:8006/api2/json/nodes

# Test SSH connection
ssh -i ~/.ssh/id_rsa root@your-proxmox-host
```

## Example Complete Configuration

```json
{
  "source": {
    "hostname": "proxmox1.company.com",
    "port": 8006,
    "username": "root@pam",
    "token_name": "vm-migration-source",
    "token_value": "abc123def456ghi789jkl012mno345pqr678stu901vwx234yz",
    "verify_ssl": true
  },
  "target": {
    "hostname": "proxmox2.company.com",
    "port": 8006,
    "username": "root@pam",
    "token_name": "vm-migration-target",
    "token_value": "xyz987wvu654tsr321qpo098nml765kji432hgf210edc",
    "verify_ssl": true
  },
  "ssh": {
    "username": "root",
    "key_filename": "~/.ssh/vm_migration_key",
    "port": 22
  },
  "storage": {
    "backup_storage": "backup_pool",
    "target_storage": "main_pool",
    "transfer_method": "scp"
  }
}
```

## Next Steps

After setting up your tokens:

1. **Test the configuration**: Run the test script
2. **List VMs**: Verify you can see VMs on both hosts
3. **Create a backup**: Test backup functionality
4. **Start migrating**: Begin with a test VM

Remember to keep your tokens secure and never commit them to version control! 