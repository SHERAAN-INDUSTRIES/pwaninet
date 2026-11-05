from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from users.models import ServiceAccount

User = get_user_model()


class Command(BaseCommand):
    help = 'Create a service account for internal API access'

    def add_arguments(self, parser):
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

        self.stdout.write(self.style.SUCCESS(f'\nService Token: {token.key}'))
        self.stdout.write(self.style.WARNING('Save this token securely! It will not be shown again.'))
        self.stdout.write(self.style.WARNING(f'Username: {username}'))
        self.stdout.write(self.style.WARNING(f'Service Account Name: {name}'))
