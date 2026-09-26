import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import json

# Add the project root to the path so we can import app.py
sys.path.insert(0, os.path.abspath('.'))

class TestDevteamEndpoints(unittest.TestCase):

    def setUp(self):
        """Set up test client and mock necessary modules."""
        # Mock external dependencies
        # Check if Flask is available in the app module before mocking
        import app
        if hasattr(app, 'Flask'):
            self.mock_flask_patcher = patch('app.Flask')
            self.mock_flask = self.mock_flask_patcher.start()
        else:
            self.mock_flask_patcher = None
            self.mock_flask = None
        
        # Import the actual app after mocking
        self.app_module = app
        
        # Mock subprocess for hermes calls
        self.mock_subprocess_patcher = patch('app.subprocess')
        self.mock_subprocess = self.mock_subprocess_patcher.start()
        
        # Mock os.environ
        self.mock_environ_patcher = patch('app.os.environ', {})
        self.mock_environ = self.mock_environ_patcher.start()

    def tearDown(self):
        """Clean up mocks."""
        if self.mock_flask_patcher:
            self.mock_flask_patcher.stop()
        self.mock_subprocess_patcher.stop()
        self.mock_environ_patcher.stop()

    @patch('app._is_section_authorized')
    def test_api_devteam_report_issue_success(self, mock_auth):
        """Test successful creation of a devteam issue report."""
        mock_auth.return_value = True
        
        # Mock request data
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "Task t_1234 created"
        mock_res.stderr = ""
        self.mock_subprocess.run.return_value = mock_res

        mock_request = MagicMock()
        mock_request.get_json.return_value = {
            'title': 'Test Issue',
            'body': 'This is a test issue',
            'component': 'agydashboard',
            'urgency': 'high'
        }
        mock_request.headers.get.return_value = 'TestAgent/1.0'
        
        # Replace request in app module with our mock
        original_request = self.app_module.request
        self.app_module.request = mock_request
        
        with self.app_module.app.app_context():
            try:
                # Call the function
                result = self.app_module.api_devteam_report_issue()
                
                # Verify the result
                resp = result[0] if isinstance(result, tuple) else result
                data = resp.get_json() if hasattr(resp, 'get_json') else resp
                self.assertTrue(data.get('success'))
            finally:
                # Restore original request
                self.app_module.request = original_request

    @patch('app._is_section_authorized')
    def test_api_devteam_report_issue_missing_title(self, mock_auth):
        """Test error when title is missing."""
        mock_auth.return_value = True
        
        # Mock request data with missing title
        mock_request = MagicMock()
        mock_request.get_json.return_value = {
            'body': 'This is a test issue'
        }
        mock_request.headers.get.return_value = 'TestAgent/1.0'
        
        # Replace request in app module with our mock
        original_request = self.app_module.request
        self.app_module.request = mock_request
        
        with self.app_module.app.app_context():
            try:
                # Call the function
                result = self.app_module.api_devteam_report_issue()
                
                # Verify error response
                data = result[0].get_json() if hasattr(result[0], 'get_json') else result[0]
                self.assertFalse(data.get('success'))
                self.assertEqual(result[1], 400)
            finally:
                # Restore original request
                self.app_module.request = original_request

    @patch('app._is_section_authorized')
    def test_api_devteam_report_issue_not_authorized(self, mock_auth):
        """Test error when user is not authorized."""
        mock_auth.return_value = False
        
        with self.app_module.app.app_context():
            # Call the function
            result = self.app_module.api_devteam_report_issue()
            
            # Verify error response
            data = result[0].get_json() if hasattr(result[0], 'get_json') else result[0]
            self.assertFalse(data.get('success'))
            self.assertEqual(result[1], 403)

    @patch('app._is_dashboard_authenticated')
    @patch('app.render_template')
    def test_devteam_report_form_endpoint(self, mock_render, mock_auth):
        """Test that the devteam report form endpoint exists."""
        mock_auth.return_value = True
        mock_render.return_value = 'Report Form'
        
        # Call the function
        result = self.app_module.devteam_report_form()
        
        # Verify the result
        self.assertEqual(result, 'Report Form')

    @patch('app._is_dashboard_authenticated')
    @patch('app.redirect')
    def test_devteam_report_form_endpoint_unauthenticated(self, mock_redirect, mock_auth):
        """Test that the devteam report form redirects when not authenticated."""
        mock_auth.return_value = False
        mock_redirect.return_value = 'Redirect to login'
        
        # Call the function
        result = self.app_module.devteam_report_form()
        
        # Verify the result
        self.assertEqual(result, 'Redirect to login')


if __name__ == '__main__':
    unittest.main()