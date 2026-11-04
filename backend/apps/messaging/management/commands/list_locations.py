from django.core.management.base import BaseCommand

from apps.messaging.models import Location


class Command(BaseCommand):
    help = "Print the location tree with the number of registered recipients under each node."

    def add_arguments(self, parser):
        parser.add_argument("--max-depth", type=int, default=2, help="Deepest level to print (0 = top level only). Default 2.")
        parser.add_argument("--code", help="Only print the subtree under this location code.")

    def handle(self, *args, **opts):
        queryset = Location.objects.all()
        if opts["code"]:
            root = Location.objects.filter(code=opts["code"]).first()
            if root is None:
                self.stderr.write(f"No location with code {opts['code']!r}.")
                return
            queryset = queryset.filter(path__startswith=root.path)
        nodes = list(queryset.order_by("path"))
        if not nodes:
            self.stdout.write("No locations loaded. Run: python manage.py load_locations --help")
            return
        counts = {level: queryset.filter(level=level).count() for level in dict.fromkeys(n.level for n in nodes)}
        self.stdout.write("Levels: " + ", ".join(f"{level}={count}" for level, count in counts.items()))
        for node in nodes:
            if node.depth > opts["max_depth"]:
                continue
            people = node.recipients.count() if node.is_leaf else Location.objects.filter(
                path__startswith=node.path).values_list("recipients", flat=True).exclude(recipients=None).count()
            flag = "" if node.is_active else "  [inactive]"
            self.stdout.write(f"{'  ' * node.depth}{node.name} [{node.level}] {node.code}  ({people} registered){flag}")
