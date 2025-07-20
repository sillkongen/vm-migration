
#!/usr/bin/env python3
"""
Proxmox VM Migration Script
============================

This script migrates VMs between Proxmox hosts that are not in a cluster.
It supports both local and remote migrations with various options.

Requirements:
- Python 3.6+
- paramiko (for SSH connections)
- requests (for Proxmox API)
- click (for CLI interface)

Usage:
    python proxmox_migrator.py migrate --source-host host1 --target-host host2 --vm-id 100
    python proxmox_migrator.py list-vms --host host1
    python proxmox_migrator.py backup --host host1 --vm-id 100
"""

import os
import sys
import json
import time
import logging
import subprocess
import getpass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

try:
    import click
    import paramiko
    import requests
    from requests.auth import HTTPBasicAuth
except ImportError as e:
    print(f"Missing required dependency: {e}")
    print("Please install required packages: pip install click paramiko requests")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('vm_migration.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class ProxmoxConfig:
    """Configuration for Proxmox host connection"""
    hostname: str
    port: int = 8006
    username: str = "root@pam"
    password: Optional[str] = None
    token_name: Optional[str] = None
    token_value: Optional[str] = None
    verify_ssl: bool = False

@dataclass
class VMInfo:
    """VM information structure"""
    vmid: int
    name: str
    status: str
    cpu: float
    memory: int
    disk_size: int
    storage: str

class ProxmoxAPI:
    """Proxmox API client"""
    
    def __init__(self, config: ProxmoxConfig):
        self.config = config
        self.base_url = f"https://{config.hostname}:{config.port}/api2/json"
        self.session = requests.Session()
        
        if not config.verify_ssl:
            self.session.verify = False
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    def _get_auth(self):
        """Get authentication method"""
        if self.config.token_name and self.config.token_value:
            # For token authentication, we need to set the header manually
            return None
        elif self.config.password:
            # For password authentication, we need to get a ticket first
            return None
        else:
            raise ValueError("Either password or token must be provided")
    
    def _get_ticket(self):
        """Get authentication ticket for password-based auth"""
        auth_data = {
            'username': self.config.username,
            'password': self.config.password
        }
        
        try:
            response = self.session.post(f"{self.base_url}/access/ticket", data=auth_data)
            response.raise_for_status()
            result = response.json()
            return result['data']['ticket']
        except Exception as e:
            logger.error(f"Failed to get authentication ticket: {e}")
            raise
    
    def _make_request(self, method: str, endpoint: str, **kwargs):
        """Make API request"""
        url = f"{self.base_url}{endpoint}"
        auth = self._get_auth()
        
        # Set up headers for authentication
        headers = kwargs.get('headers', {})
        if self.config.token_name and self.config.token_value:
            auth_header = f'PVEAPIToken={self.config.username}!{self.config.token_name}={self.config.token_value}'
            headers['Authorization'] = auth_header
            logger.info(f"Using token authentication: {self.config.username}!{self.config.token_name}")
            logger.info(f"Token name: '{self.config.token_name}', Token value length: {len(self.config.token_value)}")
        elif self.config.password:
            # For password authentication, we need to get a ticket first
            ticket = self._get_ticket()
            headers['Cookie'] = f'PVEAuthCookie={ticket}'
            logger.info("Using password authentication with ticket")
        else:
            logger.info("No authentication configured")
        kwargs['headers'] = headers
        
        try:
            response = self.session.request(method, url, auth=auth, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            raise
    
    def get_nodes(self) -> List[str]:
        """Get list of nodes"""
        response = self._make_request('GET', '/nodes')
        return [node['node'] for node in response['data']]
    
    def get_vms(self, node: str) -> List[VMInfo]:
        """Get list of VMs on a node"""
        response = self._make_request('GET', f'/nodes/{node}/qemu')
        logger.info(f"Raw API response: {response}")
        vms = []
        
        for vm in response['data']:
            logger.info(f"Processing VM: {vm}")
            vm_info = VMInfo(
                vmid=vm['vmid'],
                name=vm.get('name', f"VM {vm['vmid']}"),
                status=vm.get('status', 'unknown'),
                cpu=vm.get('cpu', 0),
                memory=vm.get('mem', 0),
                disk_size=vm.get('maxdisk', 0),
                storage=vm.get('storage', 'unknown')
            )
            vms.append(vm_info)
        
        logger.info(f"Processed {len(vms)} VMs")
        return vms
    
    def get_tasks(self, node: str) -> List[Dict]:
        """Get list of tasks on a node"""
        response = self._make_request('GET', f'/nodes/{node}/tasks')
        return response['data']
    
    def get_backups(self, node: str) -> List[Dict]:
        """Get list of backups on a node using SSH"""
        # Get SSH configuration
        ssh_config = self._get_ssh_config()
        
        ssh = SSHClient(
            hostname=self.config.hostname,
            username=ssh_config.get('username', 'root'),
            key_filename=ssh_config.get('key_filename'),
            password=ssh_config.get('password'),
            port=ssh_config.get('port', 22)
        )
        
        try:
            ssh.connect()
            
            # List backup files
            cmd = "ls -la /var/lib/vz/dump/vzdump-qemu-*.vma.gz 2>/dev/null | awk '{print $9, $5, $6, $7, $8}'"
            exit_code, output, error = ssh.execute_command(cmd)
            
            backups = []
            if exit_code == 0 and output.strip():
                lines = output.strip().split('\n')
                for line in lines:
                    if line.strip():
                        parts = line.split()
                        if len(parts) >= 5:
                            filename = parts[0]
                            size = parts[1]
                            date = f"{parts[2]} {parts[3]} {parts[4]}"
                            
                            # Extract VMID from filename
                            vmid = "Unknown"
                            if 'vzdump-qemu-' in filename:
                                try:
                                    vmid = filename.split('vzdump-qemu-')[1].split('-')[0]
                                except:
                                    pass
                            
                            backups.append({
                                'filename': filename,
                                'size': size,
                                'date': date,
                                'vmid': vmid
                            })
            
            return backups
            
        except Exception as e:
            logger.error(f"Failed to get backups: {e}")
            return []
        finally:
            ssh.close()
    
    def _get_ssh_config(self) -> Dict:
        """Get SSH configuration from config file"""
        # Try to load from config file
        try:
            config = load_config('proxmox_config.json')
            if 'ssh' in config:
                return config['ssh']
        except:
            pass
        
        # Return default values - prompt for password
        return {
            'username': 'root',
            'password': None,  # Will prompt if needed
            'key_filename': None,
            'port': 22
        }
    
    def get_vm_status(self, node: str, vmid: int) -> str:
        """Get VM status"""
        response = self._make_request('GET', f'/nodes/{node}/qemu/{vmid}/status/current')
        return response['data']['status']
    
    def stop_vm(self, node: str, vmid: int, timeout: int = 300):
        """Stop VM gracefully"""
        logger.info(f"Stopping VM {vmid} on {node}")
        self._make_request('POST', f'/nodes/{node}/qemu/{vmid}/status/shutdown')
        
        # Wait for VM to stop
        start_time = time.time()
        while time.time() - start_time < timeout:
            status = self.get_vm_status(node, vmid)
            if status == 'stopped':
                logger.info(f"VM {vmid} stopped successfully")
                return
            time.sleep(5)
        
        # Force stop if timeout
        logger.warning(f"Force stopping VM {vmid}")
        self._make_request('POST', f'/nodes/{node}/qemu/{vmid}/status/stop')
    
    def start_vm(self, node: str, vmid: int):
        """Start VM"""
        logger.info(f"Starting VM {vmid} on {node}")
        self._make_request('POST', f'/nodes/{node}/qemu/{vmid}/status/start')
    
    def create_backup(self, node: str, vmid: int, storage: str = 'local') -> str:
        """Create VM backup using SSH vzdump command"""
        logger.info(f"Creating backup for VM {vmid}")
        
        # Get SSH configuration
        ssh_config = self._get_ssh_config()
        
        # Create SSH connection to run vzdump
        ssh = SSHClient(
            hostname=self.config.hostname,
            username=ssh_config.get('username', 'root'),
            key_filename=ssh_config.get('key_filename'),
            password=ssh_config.get('password'),
            port=ssh_config.get('port', 22)
        )
        
        try:
            ssh.connect()
            
            # Run vzdump command
            vzdump_cmd = f"vzdump {vmid} --storage {storage} --compress gzip --mode snapshot"
            print(f"🔄 Running backup command: {vzdump_cmd}")
            
            exit_code, output, error = ssh.execute_command(vzdump_cmd)
            
            if exit_code == 0:
                # Parse the output to find the backup file
                backup_file = self._parse_vzdump_output(output, vmid)
                print(f"✅ Backup created successfully: {backup_file}")
                return backup_file
            else:
                raise Exception(f"vzdump failed: {error}")
                
        except Exception as e:
            logger.error(f"Backup creation failed: {e}")
            raise
        finally:
            ssh.close()
    
    def _parse_vzdump_output(self, output: str, vmid: int) -> str:
        """Parse vzdump output to find the backup file path"""
        lines = output.split('\n')
        
        for line in lines:
            # Look for lines like: "INFO: vzdump backup file: /var/lib/vz/dump/vzdump-qemu-100-2024_07_19-14_30_45.vma.gz"
            if 'vzdump backup file:' in line:
                backup_file = line.split('vzdump backup file:')[-1].strip()
                return backup_file
            
            # Look for lines like: "INFO: creating archive '/var/lib/vz/dump/vzdump-qemu-100-2024_07_19-14_30_45.vma.gz'"
            elif 'creating archive' in line and '.vma.gz' in line:
                backup_file = line.split("'")[1]  # Extract path between quotes
                return backup_file
        
        # Fallback: construct expected filename
        timestamp = datetime.now().strftime('%Y_%m_%d-%H_%M_%S')
        backup_file = f"/var/lib/vz/dump/vzdump-qemu-{vmid}-{timestamp}.vma.gz"
        logger.warning(f"Could not parse vzdump output, using fallback path: {backup_file}")
        return backup_file
    
    def _wait_for_task(self, node: str, task_id: str, timeout: int = 3600) -> str:
        """Wait for task completion with progress tracking"""
        start_time = time.time()
        last_progress = 0
        
        print(f"Starting task {task_id}...")
        
        while time.time() - start_time < timeout:
            try:
                response = self._make_request('GET', f'/nodes/{node}/tasks/{task_id}/status')
                task_data = response['data']
                status = task_data['status']
                
                # Get progress information
                progress = task_data.get('progress', 0)
                if isinstance(progress, str) and '%' in progress:
                    try:
                        progress = float(progress.replace('%', ''))
                    except:
                        progress = 0
                
                # Display progress
                if progress > last_progress:
                    self._display_progress(progress, task_data.get('type', 'Task'))
                    last_progress = progress
                
                if status == 'stopped':
                    exit_code = task_data.get('exitstatus', 1)
                    if exit_code == 0:
                        print(f"\n✅ Task completed successfully!")
                        return task_data.get('result', '')
                    else:
                        error_msg = task_data.get('exitstatus', 'Unknown error')
                        print(f"\n❌ Task failed with exit code {exit_code}: {error_msg}")
                        raise Exception(f"Task failed with exit code {exit_code}")
                
                elif status == 'running':
                    # Show current operation if available
                    if 'type' in task_data:
                        print(f"\r🔄 {task_data['type']} in progress... {progress:.1f}%", end='', flush=True)
                
                time.sleep(2)  # Poll every 2 seconds for better responsiveness
                
            except Exception as e:
                print(f"\n⚠️  Error polling task status: {e}")
                time.sleep(5)
        
        print(f"\n⏰ Task timeout after {timeout} seconds")
        raise Exception("Task timeout")
    
    def _display_progress(self, progress: float, task_type: str):
        """Display a nice progress bar"""
        bar_length = 40
        filled_length = int(bar_length * progress / 100)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        
        print(f"\r🔄 {task_type}: [{bar}] {progress:.1f}%", end='', flush=True)
    
    def restore_vm(self, node: str, backup_file: str, vmid: int, storage: str = 'local'):
        """Restore VM from backup"""
        logger.info(f"Restoring VM {vmid} from backup")
        
        restore_data = {
            'storage': storage,
            'vmid': vmid
        }
        
        response = self._make_request('POST', f'/nodes/{node}/storage/{storage}/content', 
                                    data=restore_data, files={'filename': open(backup_file, 'rb')})
        
        task_id = response['data']
        return self._wait_for_task(node, task_id)

    def import_backup_ssh(self, node: str, backup_file: str, vmid: int, storage: str = 'local'):
        """Import VM from backup using SSH qmrestore command with storage fallback"""
        logger.info(f"Importing VM {vmid} from backup {backup_file}")
        
        # Define storage preference order
        storage_preferences = ['local-lvm', 'local-zfs', 'local']
        
        # Get SSH configuration
        ssh_config = self._get_ssh_config()
        
        # Create SSH connection to run qmrestore
        ssh = SSHClient(
            hostname=self.config.hostname,
            username=ssh_config.get('username', 'root'),
            key_filename=ssh_config.get('key_filename'),
            password=ssh_config.get('password'),
            port=ssh_config.get('port', 22)
        )
        
        try:
            ssh.connect()
            
            # Try each storage in preference order
            for i, storage_to_try in enumerate(storage_preferences):
                print(f"🔄 Trying to import to storage: {storage_to_try}")
                
                # Run qmrestore command
                restore_cmd = f"qmrestore {backup_file} {vmid} --storage {storage_to_try}"
                print(f" Command: {restore_cmd}")
                print("=" * 60)
                
                exit_code, output, error = ssh.execute_command(restore_cmd)
                
                if exit_code == 0:
                    print(f"✅ VM {vmid} imported successfully to storage {storage_to_try}")
                    print(f"📋 Output: {output}")
                    return True
                else:
                    print(f"❌ Import to {storage_to_try} failed with exit code {exit_code}")
                    print(f"⚠️  Error: {error}")
                    
                    # Check if it's a storage content type error or storage doesn't exist
                    if ("Content type 'images' is not available" in error or 
                        "does not exist" in error):
                        print(f"🔄 Storage {storage_to_try} not suitable, trying next storage...")
                        
                        # Clean up any partial VM creation before trying next storage
                        if i < len(storage_preferences) - 1:  # Not the last storage
                            print(f"🧹 Cleaning up any partial VM {vmid} before trying next storage...")
                            cleanup_cmd = f"qm destroy {vmid} --purge 2>/dev/null || true"
                            ssh.execute_command(cleanup_cmd)
                        
                        continue
                    else:
                        # If it's a different error (like VM already exists), don't try other storages
                        raise Exception(f"Import failed: {error}")
            
            # If we get here, all storages failed
            raise Exception("All storage options failed. No suitable storage found for VM images.")
                    
        except Exception as e:
            logger.error(f"Import failed: {e}")
            raise
        finally:
            ssh.close()

class SSHClient:
    """SSH client for remote operations"""
    
    def __init__(self, hostname: str, username: str = 'root', password: Optional[str] = None, 
                 key_filename: Optional[str] = None, port: int = 22):
        self.hostname = hostname
        self.username = username
        self.password = password
        self.key_filename = key_filename
        self.port = port
        self.client = None
    
    def connect(self):
        """Connect to SSH server"""
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Expand the tilde in the key filename if it exists
            key_filename = None
            if self.key_filename:
                key_filename = os.path.expanduser(self.key_filename)
                if not os.path.exists(key_filename):
                    logger.warning(f"SSH key file not found: {key_filename}")
                    key_filename = None
            
            if key_filename and os.path.exists(key_filename):
                logger.info(f"Using SSH key authentication with {key_filename}")
                self.client.connect(
                    hostname=self.hostname,
                    port=self.port,
                    username=self.username,
                    key_filename=key_filename
                )
            elif self.password:
                logger.info("Using password authentication")
                self.client.connect(
                    hostname=self.hostname,
                    port=self.port,
                    username=self.username,
                    password=self.password
                )
            else:
                # Prompt for password
                logger.info("No SSH key or password provided, prompting for password")
                password = getpass.getpass(f"Enter SSH password for {self.username}@{self.hostname}: ")
                self.client.connect(
                    hostname=self.hostname,
                    port=self.port,
                    username=self.username,
                    password=password
                )
            
            logger.info(f"SSH connection established to {self.hostname}")
        except Exception as e:
            logger.error(f"SSH connection failed: {e}")
            raise
    
    def execute_command(self, command: str) -> Tuple[int, str, str]:
        """Execute command on remote host"""
        if not self.client:
            raise Exception("SSH client not connected")
        
        stdin, stdout, stderr = self.client.exec_command(command)
        exit_code = stdout.channel.recv_exit_status()
        output = stdout.read().decode('utf-8')
        error = stderr.read().decode('utf-8')
        
        return exit_code, output, error
    
    def copy_file(self, local_path: str, remote_path: str):
        """Copy file to remote host"""
        if not self.client:
            raise Exception("SSH client not connected")
        
        sftp = self.client.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()
    
    def close(self):
        """Close SSH connection"""
        if self.client:
            self.client.close()

class ProxmoxMigrator:
    """Main migration class"""
    
    def __init__(self, source_config: ProxmoxConfig, target_config: ProxmoxConfig):
        self.source_api = ProxmoxAPI(source_config)
        self.target_api = ProxmoxAPI(target_config)
        self.source_config = source_config
        self.target_config = target_config
    
    def migrate_vm(self, source_node: str, target_node: str, vmid: int, 
                   storage: str = 'local', backup_storage: str = 'local'):
        """Migrate VM from source to target host"""
        logger.info(f"Starting migration of VM {vmid} from {source_node} to {target_node}")
        
        try:
            # Step 1: Check VM status
            vm_status = self.source_api.get_vm_status(source_node, vmid)
            logger.info(f"VM {vmid} status: {vm_status}")
            
            # Step 2: Stop VM if running
            if vm_status == 'running':
                self.source_api.stop_vm(source_node, vmid)
            
            # Step 3: Create backup
            backup_file = self.source_api.create_backup(source_node, vmid, backup_storage)
            logger.info(f"Backup created: {backup_file}")
            
            # Step 4: Transfer backup to target
            target_host = self.target_config.hostname
            transferred_file = self._transfer_backup(backup_file, target_host, target_node, storage)
            
            # Step 5: Restore VM on target
            self.target_api.restore_vm(target_node, transferred_file, vmid, storage)
            
            # Step 6: Clean up backup on source
            self._cleanup_backup(source_node, backup_file)
            
            logger.info(f"Migration of VM {vmid} completed successfully")
            
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise
    
    def _transfer_backup(self, backup_file: str, target_host: str, target_node: str, storage: str):
        """Transfer backup file to target host"""
        logger.info(f"Transferring backup {backup_file} to {target_host}")
        
        # Get the actual backup file path
        backup_path = self._find_backup_file(backup_file)
        if not backup_path:
            raise Exception(f"Backup file not found: {backup_file}")
        
        # Transfer using SCP
        return self._transfer_via_scp(backup_path, target_host, target_node, storage)
    
    def _find_backup_file(self, backup_pattern: str) -> str:
        """Find the actual backup file from pattern"""
        # Convert pattern like "/var/lib/vz/dump/vzdump-qemu-100-*.vma.gz" to actual file
        import glob
        
        # Get the directory and pattern
        if '*' in backup_pattern:
            files = glob.glob(backup_pattern)
            if files:
                # Return the most recent file
                return max(files, key=os.path.getctime)
        
        return backup_pattern
    
    def _transfer_via_scp(self, source_file: str, target_host: str, target_node: str, storage: str) -> str:
        """Transfer file using SCP"""
        logger.info(f"Transferring {source_file} to {target_host}")
        
        # Get SSH configuration
        ssh_config = self.source_api._get_ssh_config()
        source_host = self.source_config.hostname
        
        # Create SSH connection to target host
        target_ssh = SSHClient(
            hostname=target_host,
            username=ssh_config.get('username', 'root'),
            key_filename=ssh_config.get('key_filename'),
            password=ssh_config.get('password'),
            port=ssh_config.get('port', 22)
        )
        
        try:
            target_ssh.connect()
            
            # Create target directory if it doesn't exist
            target_dir = f"/var/lib/vz/dump"
            target_ssh.execute_command(f"mkdir -p {target_dir}")
            
            # Get filename
            filename = os.path.basename(source_file)
            target_path = f"{target_dir}/{filename}"
            
            # Use sshpass if available, otherwise prompt user
            print(f"🔄 Transferring {filename} from {source_host} to {target_host}...")
            print("⚠️  You'll need to provide the source host password...")
            
            # Try using sshpass first
            sshpass_cmd = f"sshpass -p 'skari6' scp -o StrictHostKeyChecking=no root@{source_host}:{source_file} {target_path}"
            exit_code, output, error = target_ssh.execute_command(sshpass_cmd)
            
            if exit_code == 0:
                print(f"✅ File transferred successfully to {target_path}")
                return target_path
            else:
                print(f"❌ sshpass failed, trying manual approach...")
                print(f"⚠️  Error: {error}")
                
                # Manual approach - create a script that handles the password
                script_content = f"""#!/bin/bash
echo "Attempting to transfer {filename}..."
echo "You may be prompted for the password for root@{source_host}"
scp -o StrictHostKeyChecking=no root@{source_host}:{source_file} {target_path}
"""
                
                script_path = "/tmp/transfer.sh"
                target_ssh.execute_command(f"cat > {script_path} << 'EOF'\n{script_content}\nEOF")
                target_ssh.execute_command(f"chmod +x {script_path}")
                
                print("🔄 Running transfer script...")
                exit_code2, output2, error2 = target_ssh.execute_command(f"bash {script_path}")
                
                # Clean up
                target_ssh.execute_command(f"rm -f {script_path}")
                
                if exit_code2 == 0:
                    print(f"✅ File transferred successfully to {target_path}")
                    return target_path
                else:
                    raise Exception(f"Transfer failed. Error: {error2}")
                
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            raise
        finally:
            target_ssh.close()
    
    def _cleanup_backup(self, node: str, backup_file: str):
        """Clean up backup file"""
        logger.info(f"Cleaning up backup file: {backup_file}")
        # Implementation would depend on your storage setup
        pass

def load_config(config_file: str) -> Dict:
    """Load configuration from file"""
    with open(config_file, 'r') as f:
        return json.load(f)

def save_config(config: Dict, config_file: str):
    """Save configuration to file"""
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=2)

@click.group()
def cli():
    """Proxmox VM Migration Tool"""
    pass

@cli.command()
@click.option('--source-host', required=True, help='Source Proxmox host')
@click.option('--target-host', required=True, help='Target Proxmox host')
@click.option('--vm-id', required=True, type=int, help='VM ID to migrate')
@click.option('--source-node', default='pve', help='Source node name')
@click.option('--target-node', default='pve', help='Target node name')
@click.option('--storage', default='local', help='Target storage')
@click.option('--config-file', help='Configuration file path')
def migrate(source_host, target_host, vm_id, source_node, target_node, storage, config_file):
    """Migrate VM between Proxmox hosts"""
    
    # Load configuration
    if config_file and os.path.exists(config_file):
        config = load_config(config_file)
        source_config = ProxmoxConfig(**config.get('source', {}))
        target_config = ProxmoxConfig(**config.get('target', {}))
    else:
        # Use default configuration - in practice you'd prompt for credentials
        source_config = ProxmoxConfig(hostname=source_host)
        target_config = ProxmoxConfig(hostname=target_host)
    
    # Create migrator and perform migration
    migrator = ProxmoxMigrator(source_config, target_config)
    migrator.migrate_vm(source_node, target_node, vm_id, storage)

@cli.command()
@click.option('--host', required=True, help='Proxmox host')
@click.option('--node', default='pve', help='Node name')
@click.option('--config-file', help='Configuration file path')
@click.option('--username', default='root@pam', help='Username (default: root@pam)')
@click.option('--password', help='Password (if not using tokens)')
@click.option('--token-name', help='Token name (if using tokens)')
@click.option('--token-value', help='Token value (if using tokens)')
def list_nodes(host, config_file, username, password, token_name, token_value):
    """List nodes on a Proxmox host"""
    
    if config_file and os.path.exists(config_file):
        config = load_config(config_file)
        print(f"Loaded config from {config_file}")
        print(f"Source config: {config.get('source', {})}")
        proxmox_config = ProxmoxConfig(**config.get('source', {}))
        print(f"ProxmoxConfig created: hostname={proxmox_config.hostname}, username={proxmox_config.username}")
        print(f"Token name: '{proxmox_config.token_name}', Token value: {'*' * len(proxmox_config.token_value) if proxmox_config.token_value else 'None'}")
    else:
        # Use command line parameters
        proxmox_config = ProxmoxConfig(
            hostname=host,
            username=username,
            password=password,
            token_name=token_name,
            token_value=token_value
        )
    
    api = ProxmoxAPI(proxmox_config)
    nodes = api.get_nodes()
    
    print(f"\nNodes on {host}:")
    print("-" * 40)
    for node in nodes:
        print(f"  {node}")

@cli.command()
@click.option('--host', required=True, help='Proxmox host')
@click.option('--node', default='pve', help='Node name')
@click.option('--config-file', help='Configuration file path')
@click.option('--username', default='root@pam', help='Username (default: root@pam)')
@click.option('--password', help='Password (if not using tokens)')
@click.option('--token-name', help='Token name (if using tokens)')
@click.option('--token-value', help='Token value (if using tokens)')
def list_vms(host, node, config_file, username, password, token_name, token_value):
    """List VMs on a Proxmox host"""
    
    if config_file and os.path.exists(config_file):
        config = load_config(config_file)
        print(f"Loaded config from {config_file}")
        print(f"Source config: {config.get('source', {})}")
        proxmox_config = ProxmoxConfig(**config.get('source', {}))
        print(f"ProxmoxConfig created: hostname={proxmox_config.hostname}, username={proxmox_config.username}")
        print(f"Token name: '{proxmox_config.token_name}', Token value: {'*' * len(proxmox_config.token_value) if proxmox_config.token_value else 'None'}")
    else:
        # Use command line parameters
        proxmox_config = ProxmoxConfig(
            hostname=host,
            username=username,
            password=password,
            token_name=token_name,
            token_value=token_value
        )
    
    api = ProxmoxAPI(proxmox_config)
    vms = api.get_vms(node)
    
    print(f"\nVMs on {host} ({node}):")
    print("-" * 80)
    print(f"{'VMID':<6} {'Name':<20} {'Status':<10} {'CPU':<8} {'Memory':<10} {'Storage':<15}")
    print("-" * 80)
    
    for vm in vms:
        memory_mb = vm.memory // (1024 * 1024) if vm.memory > 0 else 0
        print(f"{vm.vmid:<6} {vm.name:<20} {vm.status:<10} {vm.cpu:<8} {memory_mb:<10} {vm.storage:<15}")

@cli.command()
@click.option('--host', required=True, help='Proxmox host')
@click.option('--vm-id', required=True, type=int, help='VM ID to backup')
@click.option('--node', default='pve', help='Node name')
@click.option('--storage', default='local', help='Backup storage')
@click.option('--config-file', help='Configuration file path')
def backup(host, vm_id, node, storage, config_file):
    """Create backup of a VM"""
    
    if config_file and os.path.exists(config_file):
        config = load_config(config_file)
        proxmox_config = ProxmoxConfig(**config.get('source', {}))
    else:
        proxmox_config = ProxmoxConfig(hostname=host)
    
    api = ProxmoxAPI(proxmox_config)
    backup_file = api.create_backup(node, vm_id, storage)
    print(f"Backup created: {backup_file}")

@cli.command()
def init_config():
    """Initialize configuration file"""
    config = {
        "source": {
            "hostname": "source-proxmox.example.com",
            "port": 8006,
            "username": "root@pam",
            "token_name": "your_source_token_name",
            "token_value": "your_source_token_value_here",
            "verify_ssl": False
        },
        "target": {
            "hostname": "target-proxmox.example.com",
            "port": 8006,
            "username": "root@pam",
            "token_name": "your_target_token_name",
            "token_value": "your_target_token_value_here",
            "verify_ssl": False
        },
        "ssh": {
            "username": "root",
            "key_filename": "~/.ssh/id_rsa",
            "port": 22
        },
        "storage": {
            "backup_storage": "local",
            "target_storage": "local",
            "transfer_method": "scp"
        }
    }
    
    save_config(config, 'proxmox_config.json')
    print("Configuration file 'proxmox_config.json' created.")
    print("Please edit it with your actual API tokens and SSH keys.")
    print("\nTo create API tokens in Proxmox:")
    print("1. Go to Datacenter > Permissions > API Tokens")
    print("2. Click 'Add' to create a new token")
    print("3. Set appropriate privileges (Datacenter.Allocate, VM.Allocate, etc.)")
    print("4. Copy the token name and value to your config file")

@cli.command()
@click.option('--host', required=True, help='Proxmox host')
@click.option('--node', default='pve', help='Node name')
@click.option('--config-file', help='Configuration file path')
@click.option('--username', default='root@pam', help='Username (default: root@pam)')
@click.option('--password', help='Password (if not using tokens)')
@click.option('--token-name', help='Token name (if using tokens)')
@click.option('--token-value', help='Token value (if using tokens)')
def list_tasks(host, node, config_file, username, password, token_name, token_value):
    """List running tasks on a Proxmox host"""
    
    if config_file and os.path.exists(config_file):
        config = load_config(config_file)
        proxmox_config = ProxmoxConfig(**config.get('source', {}))
    else:
        proxmox_config = ProxmoxConfig(
            hostname=host,
            username=username,
            password=password,
            token_name=token_name,
            token_value=token_value
        )
    
    api = ProxmoxAPI(proxmox_config)
    tasks = api.get_tasks(node)
    
    print(f"\nTasks on {host} ({node}):")
    print("-" * 80)
    print(f"{'Task ID':<15} {'Type':<20} {'Status':<10} {'Progress':<10} {'Start Time':<20}")
    print("-" * 80)
    
    for task in tasks:
        progress = task.get('progress', '0%')
        start_time = task.get('starttime', 'Unknown')
        if start_time != 'Unknown':
            start_time = datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S')
        
        print(f"{task['id']:<15} {task.get('type', 'Unknown'):<20} {task.get('status', 'Unknown'):<10} {progress:<10} {start_time:<20}")

@cli.command()
@click.option('--host', required=True, help='Proxmox host')
@click.option('--node', default='pve', help='Node name')
@click.option('--task-id', required=True, help='Task ID to monitor')
@click.option('--config-file', help='Configuration file path')
@click.option('--username', default='root@pam', help='Username (default: root@pam)')
@click.option('--password', help='Password (if not using tokens)')
@click.option('--token-name', help='Token name (if using tokens)')
@click.option('--token-value', help='Token value (if using tokens)')
def monitor_task(host, node, task_id, config_file, username, password, token_name, token_value):
    """Monitor a specific task with progress"""
    
    if config_file and os.path.exists(config_file):
        config = load_config(config_file)
        proxmox_config = ProxmoxConfig(**config.get('source', {}))
    else:
        proxmox_config = ProxmoxConfig(
            hostname=host,
            username=username,
            password=password,
            token_name=token_name,
            token_value=token_value
        )
    
    api = ProxmoxAPI(proxmox_config)
    result = api._wait_for_task(node, task_id)
    print(f"\nTask result: {result}")

@cli.command()
@click.option('--source-host', required=True, help='Source Proxmox host')
@click.option('--target-host', required=True, help='Target Proxmox host')
@click.option('--backup-file', required=True, help='Backup file path or pattern')
@click.option('--source-node', default='pve', help='Source node name')
@click.option('--target-node', default='pve', help='Target node name')
@click.option('--storage', default='local', help='Target storage')
@click.option('--config-file', help='Configuration file path')
def transfer_backup(source_host, target_host, backup_file, source_node, target_node, storage, config_file):
    """Transfer a backup file between Proxmox hosts"""
    
    # Load configuration
    if config_file and os.path.exists(config_file):
        config = load_config(config_file)
        source_config = ProxmoxConfig(**config.get('source', {}))
        target_config = ProxmoxConfig(**config.get('target', {}))
    else:
        # Use default configuration
        source_config = ProxmoxConfig(hostname=source_host)
        target_config = ProxmoxConfig(hostname=target_host)
    
    # Create migrator and perform transfer
    migrator = ProxmoxMigrator(source_config, target_config)
    transferred_file = migrator._transfer_backup(backup_file, target_host, target_node, storage)
    print(f"✅ Backup transferred successfully to: {transferred_file}")

@cli.command()
@click.option('--host', required=True, help='Proxmox host')
@click.option('--node', default='pve', help='Node name')
@click.option('--config-file', help='Configuration file path')
@click.option('--username', default='root@pam', help='Username (default: root@pam)')
@click.option('--password', help='Password (if not using tokens)')
@click.option('--token-name', help='Token name (if using tokens)')
@click.option('--token-value', help='Token value (if using tokens)')
def list_backups(host, node, config_file, username, password, token_name, token_value):
    """List existing backups on a Proxmox host"""
    
    if config_file and os.path.exists(config_file):
        config = load_config(config_file)
        proxmox_config = ProxmoxConfig(**config.get('source', {}))
    else:
        proxmox_config = ProxmoxConfig(
            hostname=host,
            username=username,
            password=password,
            token_name=token_name,
            token_value=token_value
        )
    
    api = ProxmoxAPI(proxmox_config)
    backups = api.get_backups(node)
    
    print(f"\nBackups on {host} ({node}):")
    print("-" * 100)
    print(f"{'Filename':<50} {'Size':<12} {'Date':<20} {'VMID':<6}")
    print("-" * 100)
    
    for backup in backups:
        print(f"{backup['filename']:<50} {backup['size']:<12} {backup['date']:<20} {backup['vmid']:<6}")

@cli.command()
@click.option('--config-file', default='proxmox_config.json', help='Configuration file path')
def test_config(config_file):
    """Test configuration file and connection"""
    if not os.path.exists(config_file):
        print(f"Configuration file '{config_file}' not found!")
        return
    
    try:
        config = load_config(config_file)
        print(f"Configuration file '{config_file}' loaded successfully.")
        
        # Test source configuration
        if 'source' in config:
            source_config = ProxmoxConfig(**config['source'])
            print(f"\nSource host: {source_config.hostname}")
            print(f"Username: {source_config.username}")
            if source_config.token_name and source_config.token_value:
                print(f"Token: {source_config.username}!{source_config.token_name}")
                print("Token authentication configured ✓")
            elif source_config.password:
                print("Password authentication configured ✓")
            else:
                print("❌ No authentication configured!")
        
        # Test target configuration
        if 'target' in config:
            target_config = ProxmoxConfig(**config['target'])
            print(f"\nTarget host: {target_config.hostname}")
            print(f"Username: {target_config.username}")
            if target_config.token_name and target_config.token_value:
                print(f"Token: {target_config.username}!{target_config.token_name}")
                print("Token authentication configured ✓")
            elif target_config.password:
                print("Password authentication configured ✓")
            else:
                print("❌ No authentication configured!")
        
        print("\nConfiguration test completed.")
        
    except Exception as e:
        print(f"Error testing configuration: {e}")

@cli.command()
@click.option('--host', required=True, help='Proxmox host')
@click.option('--backup-file', required=True, help='Backup file path on the host')
@click.option('--vm-id', required=True, type=int, help='VM ID to restore to')
@click.option('--node', default='pve', help='Node name')
@click.option('--storage', default='local', help='Storage to restore to')
@click.option('--config-file', help='Configuration file path')
@click.option('--username', default='root@pam', help='Username (default: root@pam)')
@click.option('--password', help='Password (if not using tokens)')
@click.option('--token-name', help='Token name (if using tokens)')
@click.option('--token-value', help='Token value (if using tokens)')
def import_backup(host, backup_file, vm_id, node, storage, config_file, username, password, token_name, token_value):
    """Import a backup file to create a VM"""
    
    if config_file and os.path.exists(config_file):
        config = load_config(config_file)
        # Use target configuration if available, otherwise use host parameter
        if 'target' in config:
            proxmox_config = ProxmoxConfig(**config.get('target', {}))
        else:
            proxmox_config = ProxmoxConfig(
                hostname=host,
                username=username,
                password=password,
                token_name=token_name,
                token_value=token_value
            )
    else:
        proxmox_config = ProxmoxConfig(
            hostname=host,
            username=username,
            password=password,
            token_name=token_name,
            token_value=token_value
        )
    
    api = ProxmoxAPI(proxmox_config)
    api.import_backup_ssh(node, backup_file, vm_id, storage)

if __name__ == '__main__':
    cli() 