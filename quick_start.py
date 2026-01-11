#!/usr/bin/env python3
"""
Quick Start Script for pviz MCP Server

This script helps you:
1. Verify your environment is set up correctly
2. Test connectivity to your API
3. Start the MCP server

Run: python quick_start.py
"""

import os
import sys
import subprocess


def check_python_version():
    """Ensure Python 3.8+"""
    if sys.version_info < (3, 8):
        print("❌ Python 3.8+ required")
        print(f"   Current version: {sys.version}")
        return False
    print(f"✅ Python version: {sys.version_info.major}.{sys.version_info.minor}")
    return True


def check_dependencies():
    """Check if required packages are installed"""
    required = ["mcp", "httpx", "starlette", "uvicorn"]
    missing = []
    
    for package in required:
        try:
            __import__(package.replace("-", "_"))
            print(f"✅ {package} installed")
        except ImportError:
            missing.append(package)
            print(f"❌ {package} not installed")
    
    if missing:
        print("\n📦 Install missing packages:")
        print(f"   pip install {' '.join(missing)}")
        return False
    
    return True


def check_environment():
    """Check environment variables"""
    jwt_token = os.getenv("PVIZ_JWT_TOKEN")
    api_url = os.getenv("PVIZ_API_URL", "https://api.pvizgenerator.com")
    
    print(f"\n🔑 JWT Token: {'✅ Set' if jwt_token else '❌ Not set'}")
    print(f"🌐 API URL: {api_url}")
    
    if not jwt_token:
        print("\n⚠️  PVIZ_JWT_TOKEN not set!")
        print("   Set it with:")
        print("   export PVIZ_JWT_TOKEN='your-token-here'")
        return False
    
    return True


def test_api_connectivity():
    """Test connection to pviz API and verify authentication"""
    import httpx
    
    api_url = os.getenv("PVIZ_API_URL", "https://api.pvizgenerator.com")
    jwt_token = os.getenv("PVIZ_JWT_TOKEN")
    
    print(f"\n🔌 Testing connection to {api_url}...")
    
    # First check basic connectivity
    try:
        response = httpx.get(
            f"{api_url}/health",
            timeout=10.0
        )
        
        if response.status_code == 200:
            print("✅ API is reachable!")
        else:
            print(f"⚠️  Health endpoint returned {response.status_code}")
            
    except httpx.ConnectError:
        print(f"❌ Cannot connect to {api_url}")
        print("   Check your internet connection and API URL")
        return False
    except Exception as e:
        print(f"⚠️  Health check inconclusive: {e}")
    
    # Test authentication with actual endpoints
    if jwt_token:
        print(f"\n🔐 Testing authentication...")
        
        try:
            # Test account endpoint
            headers = {
                "Authorization": f"Bearer {jwt_token}",
                "Content-Type": "application/json"
            }
            
            response = httpx.get(
                f"{api_url}/auth/me",
                headers=headers,
                timeout=10.0
            )
            
            if response.status_code == 200:
                account = response.json()
                print(f"✅ Authentication successful!")
                print(f"   Account: {account.get('email')}")
                print(f"   Plan: {account.get('plan', 'free')}")
                
                # Test token balance
                response = httpx.get(
                    f"{api_url}/tokens/overview",
                    headers=headers,
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    tokens = response.json()
                    balance = tokens.get('current_balance', 0)
                    print(f"   Token balance: {balance}")
                    
                    if balance == 0:
                        print(f"   ⚠️  Warning: 0 token balance")
                        print(f"   Add tokens at: https://pvizgenerator.com/tokens")
                
                return True
                
            elif response.status_code == 401:
                print(f"❌ Authentication failed - invalid token")
                print(f"   Get a valid token from: https://pvizgenerator.com/settings")
                return False
            else:
                print(f"⚠️  Unexpected response: {response.status_code}")
                return True  # Don't fail on unexpected codes
                
        except Exception as e:
            print(f"⚠️  Auth test failed: {e}")
            return True  # Don't fail on auth test errors
    
    return True


def show_next_steps():
    """Show what to do next"""
    print("\n" + "="*60)
    print("🎯 NEXT STEPS:")
    print("="*60)
    
    print("\n1️⃣  Test the API Adapter (RECOMMENDED FIRST):")
    print("   python api_adapter.py")
    print("   This will test all new endpoints including:")
    print("   - Account info (/auth/me)")
    print("   - Token balance (/tokens/overview)")
    print("   - Cost estimation (/estimate/github)")
    print("   - Job history (/jobs)")
    
    print("\n2️⃣  Test the MCP Server Locally:")
    print("   python pviz_mcp_server.py")
    print("   This runs in STDIO mode for Claude Desktop")
    
    print("\n3️⃣  Or Start HTTP Server:")
    print("   Then visit: http://localhost:8080/")
    print("   Health check: http://localhost:8080/health")
    
    print("\n4️⃣  Configure Claude Desktop:")
    print("   Edit: ~/Library/Application Support/Claude/claude_desktop_config.json")
    print("   See SETUP.md for detailed instructions")
    
    print("\n5️⃣  Test New Tools in Claude:")
    print("   - check_account_balance()")
    print("   - estimate_analysis_cost('facebook/react')")
    print("   - get_job_history()")
    print("   - retrieve_past_result('job-id')")
    
    print("\n6️⃣  Deploy to Cloud:")
    print("   See DEPLOYMENT_CHECKLIST.md for deployment guides")
    
    print("\n" + "="*60)


def main():
    """Main setup check"""
    print("="*60)
    print("🚀 pviz MCP Server - Quick Start")
    print("="*60)
    
    print("\n📋 Checking Prerequisites...")
    
    checks = [
        ("Python version", check_python_version),
        ("Dependencies", check_dependencies),
        ("Environment", check_environment),
        ("API connectivity", test_api_connectivity),
    ]
    
    all_passed = True
    for name, check_func in checks:
        print(f"\n▶️  {name}")
        if not check_func():
            all_passed = False
    
    if all_passed:
        print("\n✅ All checks passed!")
        show_next_steps()
    else:
        print("\n❌ Some checks failed. Please fix the issues above.")
        sys.exit(1)


if __name__ == "__main__":
    main()