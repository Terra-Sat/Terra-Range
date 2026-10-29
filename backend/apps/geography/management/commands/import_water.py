from apps.geography.import_water import Command as ImportWaterCommand


class Command(ImportWaterCommand):
    """Django management command wrapper for water-point imports.

    The reusable import logic lives in apps.geography.import_water, but Django only
    discovers management commands from `management/commands/`.
    """

    pass
