import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { NodePalette } from './NodePalette';

describe('NodePalette renders correct number of types', () => {
  const ALL_16_TYPES = [
    'input',
    'transform',
    'output',
    'llm',
    'knowledge_retrieval',
    'question_classifier',
    'parameter_extractor',
    'iteration',
    'loop',
    'variable_aggregator',
    'template',
    'http_request',
    'code',
    'tool',
    'human_input',
    'trigger',
  ];

  it('renders 16 buttons when given 16 types', () => {
    render(<NodePalette types={ALL_16_TYPES} onAdd={() => {}} onDropNode={() => {}} />);

    // Check that there are 16 buttons with testid flow-palette-{type}
    for (const type of ALL_16_TYPES) {
      const button = screen.getByTestId(`flow-palette-${type}`);
      expect(button).toBeInTheDocument();
    }

    // Also check that there are exactly 16 buttons
    const buttons = screen.getAllByTestId(/^flow-palette-/);
    expect(buttons).toHaveLength(16);
  });
});