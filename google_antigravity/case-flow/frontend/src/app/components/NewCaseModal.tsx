import { Modal, Button, TextInput, Select, Textarea, Stack } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useState } from 'react';
import { apiUrl } from '../lib/api';

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
    },
    validate: {
        summary: (value) => (value.length < 5 ? 'Summary must have at least 5 letters' : null),
    },
  });

  const handleSubmit = async (values: typeof form.values) => {
    setLoading(true);
    try {
      const response = await fetch(apiUrl('/api/cases/'), {
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
