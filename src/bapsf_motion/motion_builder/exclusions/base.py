"""Module that defines the `BaseExclusion` abstract class."""
__all__ = ["BaseExclusion"]

import numpy as np
import re
import xarray as xr

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Union

from bapsf_motion.motion_builder.item import MBItem


class BaseExclusion(ABC, MBItem):
    """
    Abstract base class for :term:`motion exclusion` classes.

    Parameters
    ----------
    ds: `~xr.Dataset`
        The `xarray` `~xarray.Dataset` the motion builder configuration
        is constructed in.

    skip_ds_add: bool
        If `True`, then skip generating the `~xarray.DataArray`
        corresponding to the :term:`exclusion layer` and skip adding it
        to the `~xarray.Dataset`. (DEFAULT: `False`)

    kwargs:
        Keyword arguments that are specific to the subclass.
    """

    # TODO: Can we define a __del__ that properly removes an exclusion
    #       and its dependencies from the motion builder dataset?
    # TODO: Rework _generate_exclusion() and regenerate_exclusion()
    #       to match the workflow of BaseLayer._generate_point_matrix(),
    #       BaseLayer._generate_point_matrix_da(), and
    #       BaseLayer.regenerate_point_matrix().

    _exclusion_type = NotImplemented  # type: str

    def __init__(
            self, ds: xr.Dataset, *, skip_ds_add: bool = False, **kwargs
    ):
        self._config_keys = {"type"}.union(set(kwargs.keys()))
        self._inputs = kwargs
        self.skip_ds_add = skip_ds_add

        self.composed_exclusions = []  # type: List[BaseExclusion]
        """
        List of dependent :term:`motion exclusions` used to make this
        more complex :term:`motion exclusion`.
        """

        super().__init__(
            ds=ds,
            base_name="mask_ex",
            name_pattern=re.compile(r"mask_ex(?P<number>[0-9]+)"),
        )

        self._validate_inputs()

        if self.skip_ds_add:
            return

        # store this mask to the Dataset
        self.regenerate_exclusion()

        # update the global mask
        self.update_global_mask()

    @property
    def config(self) -> Dict[str, Any]:
        """
        Dictionary containing the full configuration of the
        :term:`motion exclusion`.
        """
        config = {}
        for key in self._config_keys:
            if key == "type":
                config[key] = self.exclusion_type
            else:
                val = self.inputs[key]
                if isinstance(val, np.ndarray):
                    val = val.tolist()
                config[key] = val if not isinstance(val, np.generic) else val.item()
        return config

    @property
    def exclusion_type(self) -> str:
        """
        String naming the :term:`motion exclusion` type.  This is unique
        among all subclasses of `BaseExclusion`.
        """
        return self._exclusion_type

    @property
    def exclusion(self) -> xr.DataArray:
        """
        The `~xarray.DataArray` associate with the exclusion.  If the
        exclusion layer has not been generated, then it will be done
        automatically.

        An exclusion `~xarray.DataArray` is a boolean array the behaves
        like a mask to define where a probe can and can not be placed.
        """
        try:
            return self.item
        except KeyError:
            return self._generate_exclusion()

    @property
    def inputs(self) -> Dict[str, Any]:
        """
        A dictionary of the configuration inputs passed during layer
        instantiation.
        """
        return self._inputs

    @abstractmethod
    def _generate_exclusion(self) -> Union[np.ndarray, xr.DataArray]:
        """
        Generate and return a boolean array of the same size and
        shape as :attr:`mask` for the :term:`exclusion layer`.
        """
        ...

    @abstractmethod
    def _validate_inputs(self) -> None:
        """
        Validate the input arguments passed during instantiation.
        These inputs are stored in :attr:`inputs`.
        """
        ...

    def is_excluded(self, point):
        """
        Check if ``point`` resides in an excluded region defined by
        this :term:`motion exclusion`.

        Parameters
        ----------
        point: :term:`array_like`
            An :term:`array_like` variable that must have a length
            equal to :attr:`mspace_ndims`.

        Returns
        -------
        bool
            `True` if the point resides in an excluded region defined
            by this :term:`motion exclusion`, otherwise `False`.
        """
        # True if the point is excluded, False if the point is included
        if len(point) != self.mspace_ndims:
            raise ValueError

        select = {}
        for ii, dim_name in enumerate(self.mspace_dims):
            select[dim_name] = point[ii]

        return not bool(self.exclusion.sel(method="nearest", **select).data)

    def regenerate_exclusion(self):
        """
        Re-generate the :term:`motion exclusion`, i.e.
        :attr:`exclusion`.
        """
        if self.skip_ds_add:
            raise RuntimeError(
                f"For exclusion {self.name} skip_ds_add={self.skip_ds_add} and thus "
                f"the exclusion can not be regenerated and updated in the Dataset.  "
                f"To get the exclusion matrix use the 'ex.exclusion' property."
            )

        self._ds[self.name] = self._generate_exclusion()

    def update_global_mask(self):
        """
        Update the global :attr:`mask` to include the exclusions from
        this :term:`exclusion layer`.
        """
        if self.skip_ds_add:
            raise RuntimeError(
                f"For exclusion {self.name} skip_ds_add={self.skip_ds_add} and thus "
                f"the exclusion can not be merged into the global maks."
            )

        self._ds[self.mask_name] = np.logical_and(
            self.mask,
            self.exclusion,
        )
