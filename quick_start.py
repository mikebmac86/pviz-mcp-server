#!/usr/bin/env python3
"""
Quick Start Script for pviz MCP Server

This script helps you:
1. Verify your environment is set up correctly
2. Test connectivity to the pviz API
3. Validate authentication and token balance
4. Guide you through next steps

Run: python quick_start.py
"""

import os
import sys


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
    required = ["mcp", "httpx"]
    optional = ["fastapi", "uvicorn"]
    
    missing = []
    
    print("\n📦 Required packages:")
    for package in required:
        try:
            __import__(package.replace("-", "_"))
            print(f"   ✅ {package}")
        except ImportError:
            missing.append(package)
            print(f"   ❌ {package} not installed")
    
    print("\n📦 Optional packages (for HTTP mode):")
    for package in optional:
        try:
            __import__(package.replace("-", "_"))
            print(f"   ✅ {package}")
        except ImportError:
            print(f"   ⚠️  {package} not installed (only needed for HTTP mode)")
    
    if missing:
        print("\n📥 Install missing packages:")
        print(f"   pip install -r requirements.txt")
        print(f"   OR: pip install {' '.join(missing)}")
        return False
    
    return True


def check_environment():
    """Check environment variables"""
    jwt_token = os.getenv("PVIZ_JWT_TOKEN")
    api_url = os.getenv("PVIZ_API_URL", "https://api.pvizgenerator.com")
    
    print(f"\n🔑 Environment Variables:")
    print(f"   PVIZ_JWT_TOKEN: {'✅ Set' if jwt_token else '❌ Not set'}")
    print(f"   PVIZ_API_URL: {api_url}")
    
    if not jwt_token:
        print("\n⚠️  PVIZ_JWT_TOKEN not set!")
        print("\n   Get your token:")
        print("   1. Sign up at https://pvizgenerator.com")
        print("   2. Go to Dashboard → Settings → API Keys")
        print("   3. Copy your JWT token")
        print("\n   Then set it:")
        print("   export PVIZ_JWT_TOKEN='your-token-here'")
        return False
    
    return True


def test_api_connectivity():
    """Test connection to pviz API and verify authentication"""
    try:
        import httpx
    except ImportError:
        print("\n⚠️  httpx not installed, skipping API tests")
        return True
    
    api_url = os.getenv("PVIZ_API_URL", "https://api.pvizgenerator.com")
    jwt_token = os.getenv("PVIZ_JWT_TOKEN")
    
    print(f"\n🔌 Testing API Connection:")
    
    # Test basic connectivity
    try:
        response = httpx.get(
            f"{api_url}/health",
            timeout=10.0,
            follow_redirects=True
        )
        
        if response.status_code == 200:
            print(f"   ✅ API is reachable ({api_url})")
        else:
            print(f"   ⚠️  Health endpoint returned {response.status_code}")
            
    except httpx.ConnectError:
        print(f"   ❌ Cannot connect to {api_url}")
        print(f"   Check your internet connection and API URL")
        return False
    except Exception as e:
        print(f"   ⚠️  Health check failed: {e}")
        return False
    
    # Test authentication
    if jwt_token:
        print(f"\n🔐 Testing Authentication:")
        
        try:
            headers = {
                "Authorization": f"Bearer {jwt_token}",
                "Content-Type": "application/json"
            }
            
            # Test /auth/me endpoint
            response = httpx.get(
                f"{api_url}/auth/me",
                headers=headers,
                timeout=10.0
            )
            
            if response.status_code == 200:
                account = response.json()
                print(f"   ✅ Authentication successful!")
                print(f"   📧 Email: {account.get('email')}")
                print(f"   📊 Plan: {account.get('plan', 'free')}")
                
                # Test token balance
                response = httpx.get(
                    f"{api_url}/tokens/overview",
                    headers=headers,
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    tokens = response.json()
                    balance = tokens.get('balance', 0)
                    print(f"   💰 Token balance: {balance}")
                    
                    if balance == 0:
                        print(f"\n   ⚠️  Warning: 0 token balance")
                        print(f"   Purchase tokens at: https://pvizgenerator.com/pricing")
                    elif balance < 100:
                        print(f"\n   ⚠️  Low token balance (< 100)")
                        print(f"   Consider purchasing more tokens for larger analyses")
                
                return True
                
            elif response.status_code == 401:
                print(f"   ❌ Authentication failed - invalid or expired token")
                print(f"   Generate a new token at: https://pvizgenerator.com/settings")
                return False
            else:
                print(f"   ⚠️  Unexpected response: {response.status_code}")
                try:
                    error = response.json()
                    print(f"   Error: {error.get('detail', 'Unknown error')}")
                except:
                    pass
                return False
                
        except httpx.TimeoutException:
            print(f"   ⚠️  Request timed out - API may be slow")
            return True
        except Exception as e:
            print(f"   ❌ Auth test failed: {e}")
            return False
    
    return True


def show_next_steps():
    """Show what to do next"""
    print("\n" + "="*70)
    print("🎯 NEXT STEPS")
    print("="*70)
    
    print("\n📖 DOCUMENTATION GUIDE:")
    print("   Start here → README.md (overview)")
    print("   Setup guide → SETUP.md (installation & deployment)")
    print("   Quick help → QUICK_REFERENCE.md (one-page cheat sheet)")
    
    print("\n✅ RECOMMENDED PATH:")
    
    print("\n   1️⃣  Test the API adapter:")
    print("      python api_adapter.py")
    print("      → Tests all backend endpoints")
    print("      → Validates authentication")
    print("      → Checks token balance")
    
    print("\n   2️⃣  Choose your mode:")
    print("      STDIO Mode (Claude Desktop):")
    print("         python pviz_mcp_server.py")
    print("      ")
    print("      HTTP Mode (Cloud/Web):")
    print("         python pviz_mcp_http.py")
    print("         → Visit http://localhost:8080/health")
    
    print("\n   3️⃣  Configure Claude Desktop (STDIO mode):")
    print("      Edit: ~/Library/Application Support/Claude/claude_desktop_config.json")
    print("      See: SETUP.md → 'Integration with Claude Desktop'")
    
    print("\n   4️⃣  Test the MCP tools in Claude:")
    print("      Try these queries:")
    print("      • 'Check my pviz account balance'")
    print("      • 'Estimate cost for django/django'")
    print("      • 'Analyze https://github.com/flask/flask'")
    print("      • 'Show my recent analyses'")
    
    print("\n   5️⃣  Deploy to production (optional):")
    print("      See: INTEGRATION_GUIDE.md")
    print("      Options: Google Cloud Run, AWS ECS, Fly.io, Docker Compose")
    
    print("\n🔧 AVAILABLE TOOLS (see TOOLS_REFERENCE.md):")
    print("   • check_account_balance - View account info")
    print("   • estimate_analysis_cost - Get cost before analyzing")
    print("   • analyze_repository - Full dependency analysis")
    print("   • get_job_history - View past analyses")
    print("   • retrieve_past_result - Re-download old results")
    print("   • get_circular_dependencies - Find dependency cycles")
    print("   • get_repository_metrics - Extract metrics")
    print("   • compare_repositories - Compare two projects")
    print("   • get_analysis_status - Check job status")
    print("   • download_dependency_graph - Get full graph JSON")
    
    print("\n📚 MORE RESOURCES:")
    print("   • API_ENDPOINTS.md - Complete API reference")
    print("   • EXAMPLES.md - Real conversation examples")
    print("   • ARCHITECTURE.md - System design & diagrams")
    print("   • FAQ.md - 50+ common questions answered")
    print("   • TROUBLESHOOTING.md - Common issues & fixes")
    
    print("\n💡 PRO TIPS:")
    print("   • Always use estimate_analysis_cost before analyzing")
    print("   • Check token balance regularly")
    print("   • Use retrieve_past_result to avoid re-analyzing")
    print("   • See EXAMPLES.md for conversation patterns")
    
    print("\n" + "="*70)


def main():
    """Main setup check"""
    print("="*70)
    print("🚀 pviz MCP Server - Quick Start")
    print("="*70)
    print("\nVersion 2.0 - Comprehensive dependency analysis for Python, TypeScript,")
    print("JavaScript, Java, and Go repositories")
    
    print("\n📋 Running Environment Checks...")
    
    checks = [
        ("Python version", check_python_version),
        ("Dependencies", check_dependencies),
        ("Environment variables", check_environment),
        ("API connectivity", test_api_connectivity),
    ]
    
    all_passed = True
    for name, check_func in checks:
        if not check_func():
            all_passed = False
    
    if all_passed:
        print("\n" + "="*70)
        print("✅ ALL CHECKS PASSED!")
        print("="*70)
        print("\nYour environment is ready to use the pviz MCP server.")
        show_next_steps()
        return 0
    else:
        print("\n" + "="*70)
        print("❌ SOME CHECKS FAILED")
        print("="*70)
        print("\nPlease fix the issues above before proceeding.")
        print("\nFor help:")
        print("  • See SETUP.md for installation instructions")
        print("  • See TROUBLESHOOTING.md for common issues")
        print("  • Contact support@pvizgenerator.com")
        return 1


if __name__ == "__main__":
    sys.exit(main())