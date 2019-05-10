"""
Build database model definitions
"""

# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from datetime import datetime

from django.contrib.auth.models import User
from django.utils.translation import ugettext as _
from django.core.exceptions import ValidationError

from django.urls import reverse
from django.db import models, transaction
from django.db.models import Sum
from django.core.validators import MinValueValidator

from stock.models import StockItem
from part.models import BomItem


class Build(models.Model):
    """ A Build object organises the creation of new parts from the component parts.

    Attributes:
        part: The part to be built (from component BOM items)
        title: Brief title describing the build (required)
        quantity: Number of units to be built
        status: Build status code
        batch: Batch code transferred to build parts (optional)
        creation_date: Date the build was created (auto)
        completion_date: Date the build was completed
        URL: External URL for extra information
        notes: Text notes
    """

    def __str__(self):
        return "Build {q} x {part}".format(q=self.quantity, part=str(self.part))

    def get_absolute_url(self):
        return reverse('build-detail', kwargs={'pk': self.id})

    part = models.ForeignKey('part.Part', on_delete=models.CASCADE,
                             related_name='builds',
                             limit_choices_to={
                                 'buildable': True,
                                 'active': True
                             },
                             help_text='Select part to build',
                             )
    
    title = models.CharField(
        blank=False,
        max_length=100,
        help_text='Brief description of the build')
    
    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text='Number of parts to build'
    )
    
    # Build status codes
    PENDING = 10  # Build is pending / active
    ALLOCATED = 20  # Parts have been removed from stock
    CANCELLED = 30  # Build was cancelled
    COMPLETE = 40  # Build is complete

    #: Build status codes
    BUILD_STATUS_CODES = {PENDING: _("Pending"),
                          ALLOCATED: _("Allocated"),
                          CANCELLED: _("Cancelled"),
                          COMPLETE: _("Complete"),
                          }

    status = models.PositiveIntegerField(default=PENDING,
                                         choices=BUILD_STATUS_CODES.items(),
                                         validators=[MinValueValidator(0)],
                                         help_text='Build status')
    
    batch = models.CharField(max_length=100, blank=True, null=True,
                             help_text='Batch code for this build output')
    
    creation_date = models.DateField(auto_now=True, editable=False)
    
    completion_date = models.DateField(null=True, blank=True)

    completed_by = models.ForeignKey(User,
                                     on_delete=models.SET_NULL,
                                     blank=True, null=True,
                                     related_name='builds_completed'
                                     )
    
    URL = models.URLField(blank=True, help_text='Link to external URL')

    notes = models.TextField(blank=True, help_text='Extra build notes')
    """ Notes attached to each build output """

    @transaction.atomic
    def cancelBuild(self, user):
        """ Mark the Build as CANCELLED

        - Delete any pending BuildItem objects (but do not remove items from stock)
        - Set build status to CANCELLED
        - Save the Build object
        """

        for item in self.allocated_stock.all():
            item.delete()

        # Date of 'completion' is the date the build was cancelled
        self.completion_date = datetime.now().date()
        self.completed_by = user

        self.status = self.CANCELLED
        self.save()

    def getAutoAllocations(self):
        """ Return a list of parts which will be allocated
        using the 'AutoAllocate' function.

        For each item in the BOM for the attached Part:

        - If there is a single StockItem, use that StockItem
        - Take as many parts as available (up to the quantity required for the BOM)
        - If there are multiple StockItems available, ignore (leave up to the user)

        Returns:
            A list object containing the StockItem objects to be allocated (and the quantities)
        """

        allocations = []

        for item in self.part.bom_items.all():

            # How many parts required for this build?
            q_required = item.quantity * self.quantity

            stock = StockItem.objects.filter(part=item.sub_part)

            # Only one StockItem to choose from? Default to that one!
            if len(stock) == 1:
                stock_item = stock[0]

                # Check that we have not already allocated this stock-item against this build
                build_items = BuildItem.objects.filter(build=self, stock_item=stock_item)

                if len(build_items) > 0:
                    continue

                # Are there any parts available?
                if stock_item.quantity > 0:

                    # Only take as many as are available
                    if stock_item.quantity < q_required:
                        q_required = stock_item.quantity

                    allocation = {
                        'stock_item': stock_item,
                        'quantity': q_required,
                    }

                    allocations.append(allocation)

        return allocations

    @transaction.atomic
    def unallocateStock(self):
        """ Deletes all stock allocations for this build. """

        BuildItem.objects.filter(build=self.id).delete()

    @transaction.atomic
    def autoAllocate(self):
        """ Run auto-allocation routine to allocate StockItems to this Build.

        Returns a list of dict objects with keys like:

            {
                'stock_item': item,
                'quantity': quantity,
            }

        See: getAutoAllocations()
        """

        allocations = self.getAutoAllocations()

        for item in allocations:
            # Create a new allocation
            build_item = BuildItem(
                build=self,
                stock_item=item['stock_item'],
                quantity=item['quantity'])

            build_item.save()

    @transaction.atomic
    def completeBuild(self, location, user):
        """ Mark the Build as COMPLETE

        - Takes allocated items from stock
        - Delete pending BuildItem objects
        """

        for item in self.allocated_stock.all():
            
            # Subtract stock from the item
            item.stock_item.take_stock(
                item.quantity,
                user,
                'Removed {n} items to build {m} x {part}'.format(
                    n=item.quantity,
                    m=self.quantity,
                    part=self.part.name
                )
            )

            # Delete the item
            item.delete()

        # Mark the date of completion
        self.completion_date = datetime.now().date()

        self.completed_by = user

        # Add stock of the newly created item
        item = StockItem.objects.create(
            part=self.part,
            location=location,
            quantity=self.quantity,
            batch=str(self.batch) if self.batch else '',
            notes='Built {q} on {now}'.format(
                q=self.quantity,
                now=str(datetime.now().date())
            )
        )

        item.save()

        # Finally, mark the build as complete
        self.status = self.COMPLETE
        self.save()

    def getRequiredQuantity(self, part):
        """ Calculate the quantity of <part> required to make this build.
        """

        try:
            item = BomItem.objects.get(part=self.part.id, sub_part=part.id)
            return item.quantity * self.quantity
        except BomItem.DoesNotExist:
            return 0

    def getAllocatedQuantity(self, part):
        """ Calculate the total number of <part> currently allocated to this build
        """

        allocated = BuildItem.objects.filter(build=self.id, stock_item__part=part.id).aggregate(Sum('quantity'))

        q = allocated['quantity__sum']

        if q:
            return int(q)
        else:
            return 0

    def getUnallocatedQuantity(self, part):
        """ Calculate the quantity of <part> which still needs to be allocated to this build.

        Args:
            Part - the part to be tested

        Returns:
            The remaining allocated quantity
        """

        return max(self.getRequiredQuantity(part) - self.getAllocatedQuantity(part), 0)

    @property
    def required_parts(self):
        """ Returns a dict of parts required to build this part (BOM) """
        parts = []

        for item in self.part.bom_items.all():
            part = {'part': item.sub_part,
                    'per_build': item.quantity,
                    'quantity': item.quantity * self.quantity,
                    'allocated': self.getAllocatedQuantity(item.sub_part)
                    }

            parts.append(part)

        return parts

    @property
    def can_build(self):
        """ Return true if there are enough parts to supply build """

        for item in self.required_parts:
            if item['part'].total_stock < item['quantity']:
                return False

        return True

    @property
    def is_active(self):
        """ Is this build active? An active build is either:

        - PENDING
        - HOLDING
        """

        return self.status in [
            self.PENDING,
        ]

    @property
    def is_complete(self):
        """ Returns True if the build status is COMPLETE """
        return self.status == self.COMPLETE


class BuildItem(models.Model):
    """ A BuildItem links multiple StockItem objects to a Build.
    These are used to allocate part stock to a build.
    Once the Build is completed, the parts are removed from stock and the
    BuildItemAllocation objects are removed.

    Attributes:
        build: Link to a Build object
        stock_item: Link to a StockItem object
        quantity: Number of units allocated
    """

    def get_absolute_url(self):
        # TODO - Fix!
        return '/build/item/{pk}/'.format(pk=self.id)
        # return reverse('build-detail', kwargs={'pk': self.id})

    class Meta:
        unique_together = [
            ('build', 'stock_item'),
        ]

    def clean(self):
        """ Check validity of the BuildItem model.
        The following checks are performed:

        - StockItem.part must be in the BOM of the Part object referenced by Build
        - Allocation quantity cannot exceed available quantity
        """
        
        super(BuildItem, self).clean()

        errors = {}

        if self.stock_item is not None and self.stock_item.part is not None:
            if self.stock_item.part not in self.build.part.required_parts():
                errors['stock_item'] = [_("Selected stock item not found in BOM for part '{p}'".format(p=self.build.part.name))]
            
        if self.stock_item is not None and self.quantity > self.stock_item.quantity:
            errors['quantity'] = [_("Allocated quantity ({n}) must not exceed available quantity ({q})".format(
                n=self.quantity,
                q=self.stock_item.quantity
            ))]

        if len(errors) > 0:
            raise ValidationError(errors)

    build = models.ForeignKey(
        Build,
        on_delete=models.CASCADE,
        related_name='allocated_stock',
        help_text='Build to allocate parts'
    )

    stock_item = models.ForeignKey(
        'stock.StockItem',
        on_delete=models.CASCADE,
        related_name='allocations',
        help_text='Stock Item to allocate to build',
    )

    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text='Stock quantity to allocate to build'
    )
