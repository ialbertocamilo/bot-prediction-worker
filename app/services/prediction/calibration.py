"""
Post-hoc probability calibration for Dixon-Coles predictions.

Implements Platt scaling (logistic calibration) trained on historical
predictions vs. outcomes.  Applied as an optional post-processing step
after the core Dixon-Coles model produces raw probabilities.

Usage:
    calibrator = PlattCalibrator()
    calibrator.fit(predicted_probs, actual_outcomes)   # 1=event, 0=no
    calibrated = calibrator.transform(new_probs)
"""
from __future__ import annotations

import logging
import math

import numpy as np

logger = logging.getLogger(__name__)

_MIN_SAMPLES = 50


class PlattCalibrator:
    """Platt scaling — fits a logistic curve to map raw probs → calibrated probs.

    Parameters A, B are optimised so that:
        calibrated = 1 / (1 + exp(A * logit(raw) + B))

    Falls back to identity (no-op) when insufficient data.
    """

    def __init__(self) -> None:
        self._a: float = 1.0  # slope (identity default)
        self._b: float = 0.0  # intercept (identity default)
        self._fitted: bool = False

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    def fit(
        self,
        predicted: np.ndarray | list[float],
        actual: np.ndarray | list[int],
    ) -> None:
        """Fit Platt scaling parameters from historical predictions.

        Args:
            predicted: Raw model probabilities in [0, 1].
            actual: Binary outcomes (1 = event occurred, 0 = did not).
        """
        predicted = np.asarray(predicted, dtype=np.float64)
        actual = np.asarray(actual, dtype=np.float64)

        if len(predicted) < _MIN_SAMPLES:
            logger.info("Platt calibration skipped: %d samples < %d minimum", len(predicted), _MIN_SAMPLES)
            self._a, self._b, self._fitted = 1.0, 0.0, False
            return

        # Clip to avoid log(0)
        eps = 1e-7
        p = np.clip(predicted, eps, 1.0 - eps)
        logits = np.log(p / (1.0 - p))

        # Newton's method for Platt scaling (Platt 1999)
        # Targets use regularized prior:  t+ = (N+ + 1)/(N+ + 2), t- = 1/(N- + 2)
        n_pos = actual.sum()
        n_neg = len(actual) - n_pos
        t_pos = (n_pos + 1.0) / (n_pos + 2.0)
        t_neg = 1.0 / (n_neg + 2.0)
        targets = actual * t_pos + (1.0 - actual) * t_neg

        a, b = 0.0, math.log((n_neg + 1.0) / (n_pos + 1.0))
        converge_tol = 1e-7

        for iteration in range(100):
            f = 1.0 / (1.0 + np.exp(-(a * logits + b)))
            f = np.clip(f, eps, 1.0 - eps)

            d1a = np.sum((f - targets) * logits)
            d1b = np.sum(f - targets)

            d2a = np.sum(f * (1.0 - f) * logits * logits)
            d2b = np.sum(f * (1.0 - f))
            d2ab = np.sum(f * (1.0 - f) * logits)

            det = d2a * d2b - d2ab * d2ab
            if abs(det) < 1e-12:
                break

            da = (d2b * d1a - d2ab * d1b) / det
            db = (d2a * d1b - d2ab * d1a) / det
            a -= da
            b -= db

            if abs(da) + abs(db) < converge_tol:
                logger.debug("Platt converged at iteration %d", iteration + 1)
                break

        self._a = float(a)
        self._b = float(b)
        self._fitted = True
        logger.info("Platt calibration fitted: A=%.4f, B=%.4f (n=%d)", self._a, self._b, len(predicted))

    def transform(self, raw_prob: float) -> float:
        """Calibrate a single probability value."""
        if not self._fitted:
            return raw_prob
        eps = 1e-7
        p = max(eps, min(1.0 - eps, raw_prob))
        logit = math.log(p / (1.0 - p))
        return 1.0 / (1.0 + math.exp(-(self._a * logit + self._b)))

    def transform_array(self, raw_probs: np.ndarray) -> np.ndarray:
        """Calibrate an array of probabilities."""
        if not self._fitted:
            return raw_probs
        eps = 1e-7
        p = np.clip(raw_probs, eps, 1.0 - eps)
        logits = np.log(p / (1.0 - p))
        return 1.0 / (1.0 + np.exp(-(self._a * logits + self._b)))


class MultiClassPlattCalibrator:
    """Three independent Platt calibrators for 1X2 outcomes.

    Each outcome (home / draw / away) gets its own logistic calibration
    curve, avoiding the bias that occurs when a single calibrator trained
    on one outcome class is applied to all three.
    """

    def __init__(self) -> None:
        self.home = PlattCalibrator()
        self.draw = PlattCalibrator()
        self.away = PlattCalibrator()

    @property
    def is_fitted(self) -> bool:
        return self.home.is_fitted or self.draw.is_fitted or self.away.is_fitted

    def fit(
        self,
        p_home: np.ndarray,
        p_draw: np.ndarray,
        p_away: np.ndarray,
        actual_outcomes: np.ndarray,
    ) -> None:
        """Fit three calibrators from historical predictions.

        Args:
            p_home: Raw home-win probabilities.
            p_draw: Raw draw probabilities.
            p_away: Raw away-win probabilities.
            actual_outcomes: String outcomes ("HOME", "DRAW", "AWAY").
        """
        home_actual = np.array([1.0 if o == "HOME" else 0.0 for o in actual_outcomes])
        draw_actual = np.array([1.0 if o == "DRAW" else 0.0 for o in actual_outcomes])
        away_actual = np.array([1.0 if o == "AWAY" else 0.0 for o in actual_outcomes])

        self.home.fit(p_home, home_actual)
        self.draw.fit(p_draw, draw_actual)
        self.away.fit(p_away, away_actual)

        logger.info(
            "MultiClass calibration: home=%s, draw=%s, away=%s",
            self.home.is_fitted, self.draw.is_fitted, self.away.is_fitted,
        )

    def calibrate_1x2(
        self,
        p_home: float,
        p_draw: float,
        p_away: float,
    ) -> tuple[float, float, float]:
        """Calibrate and re-normalise 1X2 probabilities.

        Returns (home, draw, away) summing to 1.0.
        Falls back to raw probabilities when no calibrator is fitted.
        """
        c_home = self.home.transform(p_home)
        c_draw = self.draw.transform(p_draw)
        c_away = self.away.transform(p_away)

        total = c_home + c_draw + c_away
        if total <= 0:
            return p_home, p_draw, p_away

        return (
            round(c_home / total, 6),
            round(c_draw / total, 6),
            round(c_away / total, 6),
        )
