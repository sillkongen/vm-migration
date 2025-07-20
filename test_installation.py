#!/usr/bin/env python3
"""
Test script to verify Proxmox VM Migration Tool installation
"""

import sys
import importlib

def test_import(module_name, package_name=None):
    """Test if a module can be imported"""
    try:
        importlib.import_module(module_name)
        print(f"✓ {package_name or module_name} - OK")
        return True
    except ImportError as e:
        print(f"✗ {package_name or module_name} - FAILED: {e}")
        return False

def test_python_version():
    """Test Python version"""
    version = sys.version_info
    if version.major >= 3 and version.minor >= 6:
        print(f"✓ Python {version.major}.{version.minor}.{version.micro} - OK")
        return True
    else:
        print(f"✗ Python {version.major}.{version.minor}.{version.micro} - FAILED: Python 3.6+ required")
        return False

def test_files():
    """Test if required files exist"""
    import os
    
    required_files = [
        'proxmox_migrator.py',
        'requirements.txt',
        'README.md'
    ]
    
    all_exist = True
    for file in required_files:
        if os.path.exists(file):
            print(f"✓ {file} - OK")
        else:
            print(f"✗ {file} - MISSING")
            all_exist = False
    
    return all_exist

def test_config_file():
    """Test configuration file creation"""
    import os
    import subprocess
    
    if os.path.exists('proxmox_config.json'):
        print("✓ Configuration file exists - OK")
        return True
    
    try:
        # Try to create config file
        result = subprocess.run([
            sys.executable, 'proxmox_migrator.py', 'init-config'
        ], capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0 and os.path.exists('proxmox_config.json'):
            print("✓ Configuration file created successfully - OK")
            return True
        else:
            print(f"✗ Configuration file creation failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"✗ Configuration file creation failed: {e}")
        return False

def main():
    """Run all tests"""
    print("Proxmox VM Migration Tool - Installation Test")
    print("=" * 50)
    print()
    
    tests = [
        ("Python Version", test_python_version),
        ("Required Files", test_files),
        ("Click", lambda: test_import('click')),
        ("Paramiko", lambda: test_import('paramiko')),
        ("Requests", lambda: test_import('requests')),
        ("Configuration", test_config_file),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"Testing {test_name}...")
        if test_func():
            passed += 1
        print()
    
    print("=" * 50)
    print(f"Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! The migration tool is ready to use.")
        print("\nNext steps:")
        print("1. Edit proxmox_config.json with your actual credentials")
        print("2. Test connection: python proxmox_migrator.py list-vms --host your-host")
        print("3. Start migrating VMs!")
    else:
        print("❌ Some tests failed. Please fix the issues above before using the tool.")
        print("\nCommon solutions:")
        print("- Install missing dependencies: pip install -r requirements.txt")
        print("- Ensure you're using Python 3.6 or higher")
        print("- Check that all files are in the same directory")
    
    return passed == total

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1) 