#!/usr/bin/env python3
"""
Verification script for devteam endpoints implementation
"""

import os
import sys

def check_endpoint_functions():
    """Check that the required functions exist in app.py"""
    try:
        # Read the app.py file
        with open('app.py', 'r') as f:
            content = f.read()
        
        # Check for function definitions
        if 'def api_devteam_report_issue()' in content:
            print("✓ API endpoint function 'api_devteam_report_issue' exists")
        else:
            print("✗ API endpoint function 'api_devteam_report_issue' not found")
            return False
            
        if 'def devteam_report_form()' in content:
            print("✓ Form endpoint function 'devteam_report_form' exists")
        else:
            print("✗ Form endpoint function 'devteam_report_form' not found")
            return False
            
        return True
    except Exception as e:
        print(f"✗ Error checking functions: {e}")
        return False

def check_template_exists():
    """Check that the template file exists"""
    template_path = 'templates/devteam-report.html'
    if os.path.exists(template_path):
        print("✓ Frontend template 'devteam-report.html' exists")
        return True
    else:
        print("✗ Frontend template 'devteam-report.html' not found")
        return False

def check_docs_exist():
    """Check that documentation files exist"""
    api_spec_path = 'docs/api-spec.yaml'
    endpoint_doc_path = 'docs/devteam-report-endpoint.md'
    
    if os.path.exists(api_spec_path):
        print("✓ API specification exists")
    else:
        print("✗ API specification not found")
        return False
        
    if os.path.exists(endpoint_doc_path):
        print("✓ Endpoint documentation exists")
        return True
    else:
        print("✗ Endpoint documentation not found")
        return False

def main():
    """Main verification function"""
    print("Verifying devteam endpoints implementation...")
    
    checks = [
        check_endpoint_functions,
        check_template_exists,
        check_docs_exist
    ]
    
    passed = 0
    for check in checks:
        if check():
            passed += 1
    
    print(f"\n{passed}/{len(checks)} verification checks passed")
    
    if passed == len(checks):
        print("All verification checks passed!")
        print("\nImplementation Summary:")
        print("- Backend API endpoint: /api/devteam/report (POST)")
        print("- Frontend form route: /devteam/report (GET)")
        print("- Frontend template: templates/devteam-report.html")
        print("- Documentation: docs/api-spec.yaml, docs/devteam-report-endpoint.md")
        return 0
    else:
        print("Some verification checks failed!")
        return 1

if __name__ == '__main__':
    sys.exit(main())