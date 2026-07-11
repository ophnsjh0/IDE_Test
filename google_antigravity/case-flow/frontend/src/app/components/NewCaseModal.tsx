import { Modal, Button, TextInput, Select, Textarea, Stack, Group } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useState } from 'react';
import { apiFetch } from '../lib/api';

interface NewCaseModalProps {
  opened: boolean;
  onClose: () => void;
  onCaseCreated: () => void;
}

export default function NewCaseModal({ opened, onClose, onCaseCreated }: NewCaseModalProps) {
  const [loading, setLoading] = useState(false);

  const form = useForm({
    initialValues: {
      vendor: 'A10',
      status: 'Open',
      summary: '',
      description: '',
      action_steps: '',
      resolution: '',
      device_model: '',
      device_serial: '',
      software_version: '',
    },
    validate: {
        summary: (value) => (value.length < 5 ? 'Summary must have at least 5 letters' : null),
    },
  });

  const handleSubmit = async (values: typeof form.values) => {
    setLoading(true);
    try {
      const response = await apiFetch('/api/cases/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(values),
      });

      if (response.ok) {
        form.reset();
        onCaseCreated();
        onClose();
      } else {
        console.error('Failed to create case');
      }
    } catch (error) {
      console.error('Error creating case:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Create New Case" centered size="lg">
      <form onSubmit={form.onSubmit(handleSubmit)}>
        <Stack>
          <Select
            label="Vendor"
            placeholder="Select vendor"
            data={['A10', 'Arista', 'HPE Aruba', 'Juniper']}
            {...form.getInputProps('vendor')}
          />
          <Select
            label="Status"
            placeholder="Select status"
            data={['Open', 'Resolved', 'Pending']}
            {...form.getInputProps('status')}
          />
          <Group grow>
            <TextInput
              label="장비 모델"
              placeholder="예: TH1040-F"
              maxLength={100}
              {...form.getInputProps('device_model')}
            />
            <TextInput
              label="시리얼 번호"
              placeholder="예: TH10154022070160"
              maxLength={200}
              {...form.getInputProps('device_serial')}
            />
            <TextInput
              label="SW 버전"
              placeholder="예: 6.0.8-SP1"
              maxLength={50}
              {...form.getInputProps('software_version')}
            />
          </Group>
          <TextInput
            required
            label="Summary"
            placeholder="Brief summary of the issue"
            {...form.getInputProps('summary')}
          />
          <Textarea
            label="Description"
            placeholder="Detailed description"
            minRows={3}
            {...form.getInputProps('description')}
          />
          <Textarea
            label="Action Taken"
            placeholder="Steps taken to resolve or investigate"
            minRows={2}
            {...form.getInputProps('action_steps')}
          />
          <Textarea
            label="Resolution"
            placeholder="Final resolution if valid"
            minRows={2}
            {...form.getInputProps('resolution')}
          />
          <Button type="submit" loading={loading} fullWidth mt="md">
            Create Case
          </Button>
        </Stack>
      </form>
    </Modal>
  );
}
