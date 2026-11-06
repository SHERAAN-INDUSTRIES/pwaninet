from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from users.models import ServiceAccount
from pathlib import Path
import subprocess
import sys

User = get_user_model()


class Command(BaseCommand):
    help = 'Create a service account for internal API access'

    def add_arguments(self, parser):
        parser.add_argument('--env-file', help='Save the actual DRF service token as VASTO_SERVICE_TOKEN in this env file')
        parser.add_argument('--token-only', action='store_true', help='Emit a machine-readable VASTO_SERVICE_TOKEN line')
        parser.add_argument('--quiet-token', action='store_true', help='Do not display the service token')
        parser.add_argument('--name', required=True, help='Service account name')
        parser.add_argument('--username', required=True, help='Django username for the service account')
        parser.add_argument('--email', required=False, help='Email for the service account (optional)')

    def handle(self, *args, **options):
        name = options['name']
        username = options['username']
        email = options.get('email', f'{username}@service.local')

        try:
            user = User.objects.get(username=username)
            self.stdout.write(self.style.SUCCESS(f'Found existing user: {username}'))
        except User.DoesNotExist:
            # Create a new user for the service account
            user = User.objects.create_user(
                username=username,
                email=email,
                password=None  # No password needed for service account
            )
            user.set_unusable_password()
            user.save()
            self.stdout.write(self.style.SUCCESS(f'Created new user: {username}'))

        # Create service account
        service_account, created = ServiceAccount.objects.get_or_create(
            user=user,
            defaults={'name': name}
        )

        if not created:
            self.stdout.write(self.style.WARNING(f'Service account {name} already exists'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Created service account {name}'))

        # Get or create token
        token, created = Token.objects.get_or_create(user=user)

        if options['token_only']:
            self.stdout.write('VASTO_SERVICE_TOKEN=' + token.key)
            return

        if options['env_file']:
            env_file = Path(options['env_file']).expanduser().resolve()
            updater = Path(__file__).resolve().parents[3] / 'scripts' / 'set-env-value.py'
            subprocess.run([sys.executable, str(updater), str(env_file), 'VASTO_SERVICE_TOKEN'],
                           input=token.key, text=True, check=True)
            self.stdout.write(self.style.SUCCESS(f'Saved service token to {env_file} (value hidden).'))
        if not options['quiet_token']:
            self.stdout.write(self.style.SUCCESS(f'\nService Token: {token.key}'))
        self.stdout.write('Rerunning this command retrieves the existing token without rotating it.')
        self.stdout.write(self.style.WARNING(f'Username: {username}'))
        self.stdout.write(self.style.WARNING(f'Service Account Name: {name}'))
