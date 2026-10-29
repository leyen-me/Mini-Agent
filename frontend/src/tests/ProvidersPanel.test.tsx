import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import { ProvidersPanel } from '../components/admin/ProvidersPanel';
import { renderWithUi } from './testUtils';

describe('provider model editor', () => {
  it('loads reasoning levels from an existing model for editing', async () => {
    vi.spyOn(api.providers, 'list').mockResolvedValue([
      {
        id: 'deepseek',
        name: 'DeepSeek',
        protocol: 'openai-chat',
        base_url: 'https://api.deepseek.com',
        enabled: true,
        configured: true,
        key_hint: '已配置',
        models: [
          {
            id: 'deepseek-flash',
            name: 'DeepSeek Flash',
            context_window: 128000,
            max_output: 8192,
            capabilities: ['text', 'tools', 'reasoning'],
            reasoning_levels: ['off', 'low', 'medium', 'high', 'max'],
          },
        ],
      },
    ]);
    renderWithUi(
      <ProvidersPanel
        onToast={vi.fn()}
        onReload={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    await screen.findByText('deepseek-flash');
    fireEvent.click(
      screen.getByRole('button', { name: '编辑模型 deepseek-flash' }),
    );
    await waitFor(() =>
      expect(screen.getByLabelText('推理级别（逗号分隔）')).toHaveValue(
        'off, low, medium, high, max',
      ),
    );
    expect(screen.getByLabelText('能力（逗号分隔）')).toHaveValue(
      'text, tools, reasoning',
    );
    vi.restoreAllMocks();
  });
});
